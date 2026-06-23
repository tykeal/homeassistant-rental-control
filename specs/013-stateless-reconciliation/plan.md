<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Implementation Plan: Stateless Slot Reconciliation

**Feature**: `013-stateless-reconciliation` | **Planning Branch**:
`013-stateless-reconciliation-plan` | **Date**: 2026-06-23 | **Spec**:
[spec.md](spec.md)
**Input**: Feature specification from
`specs/013-stateless-reconciliation/spec.md` and GitHub issue #607

## Summary

Redesign the managed-slot reconciliation loop so each refresh derives the
correct Keymaster state from only two authoritative inputs: the physical
Keymaster managed-slot entities and the current calendar-derived reservations.
Persisted slot mappings are demoted to cache-only diagnostics/alias metadata and
must never decide selection, duplicate prevention, reset safety, or assignment.

The implementation will rework the existing coordinator-owned reconciliation
path (`coordinator.py:2053-2173`) into a stateless planner that observes managed
slots (`coordinator.py:1735-1958`), builds desired reservations with existing
calendar semantics (`coordinator.py:1118-1430`), matches desired reservations to
physical slots by stable trim-aware slot-name identity, and emits per-slot
actions: no-op, update in place, reset, assign, or blocked. This replaces the
persisted-authoritative Store/mapping/fence path introduced by spec 012 while
preserving buffers, Honor Event Times, manual overrides, active guest
protection, deterministic code generation, check-in sensors, and read-only
`event_N` sensors.

## Technical Context

**Language/Version**: Python ≥3.14.2
**Primary Dependencies**: Home Assistant core, Keymaster integration,
`pytest-homeassistant-custom-component`, existing `homeassistant.helpers.storage.Store`
**Storage**: Home Assistant `Store` remains available but is cache-only for
alias history and redacted diagnostics; Keymaster physical entities plus the
calendar are the correctness source
**Testing**: pytest via `uv run pytest tests/`; ruff via
`uv run ruff check custom_components/ tests/`; pre-commit hooks for reuse,
ruff, mypy, interrogate, yamllint, actionlint, and gitlint
**Target Platform**: Home Assistant custom integration on Linux, HA OS, Docker,
and HACS-managed installs using the HA asyncio event loop
**Project Type**: Single Home Assistant custom integration
**Performance Goals**: One O(max_events + max_slots) observation/matching pass
per refresh; bounded per-slot Keymaster service confirmation waits; no blocking
I/O on the event loop beyond existing executor-wrapped calendar parsing
**Constraints**: Lock-code programming is safety-critical; managed slots are
never reused until physical name and PIN are confirmed empty; `unavailable`
Keymaster text state is conservative; unmanaged slots are never modified;
callbacks must not re-enter reconciliation
**Scale/Scope**: Typical default five managed Keymaster slots and calendar
horizon from configuration; redesign touches coordinator refresh, the pure
planner, EventOverrides/slot-operation state, Keymaster callback handling,
sensors, Store migration/cache handling, diagnostics, and tests

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I: Code Quality & Testing | PASS | The plan requires pure unit coverage for the stateless planner plus integration tests for duplicate prevention, confirmed reset, Store deletion, callbacks, manual overrides, and active protection. |
| II: Atomic Commit Discipline | PASS | This PR contains only PLAN-stage docs. Future implementation can split planner, coordinator wiring, Store migration, sensor updates, and tests into atomic commits. |
| III: Licensing & Attribution | PASS | All new or modified markdown artifacts include SPDX headers. Future Python modules must keep project SPDX headers. |
| IV: Pre-Commit Integrity | PASS | No hook bypass is planned. Quickstart defines targeted validation before implementation merge. |
| V: Agent Co-Authorship & DCO | PASS | The PLAN commit uses `git commit -s` and the requested `Co-authored-by` trailer. |
| VI: User Experience Consistency | PASS | Existing entity names, `event_N` attributes, manual code/time semantics, buffers, trimming, Honor Event Times, and check-in tracking are preserved. |
| VII: Performance Requirements | PASS | Stateless planning is linear in configured slots/events and removes Store recovery/adoption loops from correctness decisions. |

**Gate result: PASS** — no violations. Proceeding to Phase 0.

## Project Structure

### Documentation (this feature)

```text
specs/013-stateless-reconciliation/
├── plan.md                    # This file
├── research.md                # Phase 0 design decisions and alternatives
├── data-model.md              # Phase 1 entities and state transitions
├── quickstart.md              # Phase 1 validation guide
├── checklists/
│   └── requirements.md        # Existing SPEC-stage checklist
└── tasks.md                   # Phase 2 output only; not created in PLAN stage
```

`contracts/` is intentionally omitted. This redesign introduces no external
HTTP, WebSocket, Home Assistant service, entity-service, or public API contract.
The internal planner interface is specified in [data-model.md](data-model.md)
instead.

### Source Code (repository root)

```text
custom_components/rental_control/
├── __init__.py                # Keep setup order; simplify startup readability
│                              # watcher once stateless retry makes timing safe
├── coordinator.py             # Observe physical slots, build desired
│                              # reservations, invoke stateless planner, apply
│                              # actions, and write cache-only Store metadata
├── event_overrides.py         # Retain the single async lock, Keymaster
│                              # service helpers, retry diagnostics, and
│                              # callback suppression; delete greedy/persisted-
│                              # authoritative allocation state
├── reconciliation.py          # Rework into a stateless pure planner (or split
│                              # to slot_planner.py if implementation prefers)
│                              # with ObservedSlot, DesiredReservation, and
│                              # SlotAction types
├── util.py                    # Reuse blank/unreadable helpers, bounded
│                              # confirmation waits, trimming, buffers, and
│                              # callback feedback handling
├── const.py                   # Keep code generation, refresh, and Store key
│                              # constants; Store schema becomes cache-only
├── sensor.py                  # No entity-name changes
└── sensors/
    ├── calsensor.py           # Keep `event_N` sensors read-only from the
    │                          # latest reconciled plan, with compatibility
    │                          # lookup from current events to desired IDs
    └── checkinsensor.py       # Continue exposing active guest state and read
                               # latest-plan slot ownership for unlock checks

tests/
├── unit/
│   ├── test_slot_reconciliation.py      # Pure stateless planner invariants
│   ├── test_event_overrides.py          # Apply actions and lock/clear safety
│   ├── test_util.py                     # Confirmed empty and service waits
│   ├── test_sensors.py                  # `event_N` read-only reflection
│   └── test_checkin_sensor.py           # Active guest protection surface
└── integration/
    ├── test_refresh_cycle.py            # End-to-end convergence scenarios
    └── test_slot_concurrency.py         # Callback/reconciliation atomicity
```

**Structure Decision**: Keep the existing single-integration layout. Rework the
current `reconciliation.py` pure planning module rather than introduce a new
runtime dependency. A `slot_planner.py` split is acceptable only if it remains an
internal module with the same pure inputs/outputs and no Store dependency.

## Phase 0 Research

Research is complete in [research.md](research.md). It resolves the required
stateless engine, stable-name identity, confirmed-reset, cache-only Store,
soonest-N/active/manual semantics, `event_N` sensors, concurrency, and deletion
of persisted-authoritative machinery. Every decision cites live code paths that
will be changed or retired.

## Phase 1 Design Artifacts

- [research.md](research.md): required and complete.
- [data-model.md](data-model.md): required and complete.
- [quickstart.md](quickstart.md): required and complete.
- `contracts/`: omitted because no external API is introduced.
- `update-agent-context.sh`: intentionally not run. The redesign adds no new
  technology, dependency, runtime, or agent-relevant tool; HA Store already
  exists in the codebase (`coordinator.py:50`, `coordinator.py:559-621`).

## Post-Design Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I: Code Quality & Testing | PASS | Quickstart enumerates must-pass acceptance tests for every safety and compatibility invariant in FR-001 through FR-021, including deleted Store cold start and duplicate/date-shift cases. |
| II: Atomic Commit Discipline | PASS | PLAN artifacts are one docs-only change; implementation can proceed as separate behavior/test commits. |
| III: Licensing & Attribution | PASS | `plan.md`, `research.md`, `data-model.md`, and `quickstart.md` include SPDX headers. |
| IV: Pre-Commit Integrity | PASS | The PR must pass repository hooks and CI without bypasses. |
| V: Agent Co-Authorship & DCO | PASS | The planned commit uses sign-off and co-authorship trailers. |
| VI: User Experience Consistency | PASS | The design preserves existing entity names, configuration options, lock-code generation semantics, check-in tracking, and diagnostics boundaries. |
| VII: Performance Requirements | PASS | Stateless matching is bounded by configured slots/events and avoids Store-adoption timing loops as a correctness dependency. |

**Gate result: PASS** — no complexity violations.

## Complexity Tracking

> No violations to justify — all constitution gates pass.

## Phase Notes

- PLAN stage stops here. Do not create `tasks.md` or modify runtime source in
  this PR.
- Implementation must keep all physical operations scoped to the configured
  Rental-Control-managed Keymaster slot range.
- The first implementation task should build the pure stateless planner tests
  before coordinator/apply-path rewiring.
