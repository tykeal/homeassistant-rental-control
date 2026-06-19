<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Implementation Plan: Slot Reconciliation

**Feature**: `012-slot-reconciliation` | **Planning Branch**:
`012-slot-reconciliation-plan` | **Date**: 2026-06-19 | **Spec**:
[spec.md](spec.md)
**Input**: Feature specification from
`/specs/012-slot-reconciliation/spec.md`

## Summary

Replace Rental Control's incremental, per-`event_N` slot assignment with one
coordinator-driven reconciliation pass on every refresh. The pass observes
current RC-managed Keymaster slots, loads persisted reservation-to-slot
metadata, computes a deterministic desired plan from current and protected
reservations, and applies only confirmed-safe diffs. `event_N` sensors become
read-only views of coordinator reconciliation state; `EventOverrides` remains
the lock-protected slot-state owner but no longer treats `_next_slot` or
`async_check_overrides()` as authoritative cleanup/assignment policy.

## Technical Context

**Language/Version**: Python ≥3.14.2
**Primary Dependencies**: Home Assistant core, Keymaster integration,
`pytest-homeassistant-custom-component`, `homeassistant.helpers.storage.Store`
**Storage**: Home Assistant storage file via `Store` for persisted
reservation identities, slot mappings, pending clears, and feed-miss counts;
Keymaster entities remain the physical source for actual slot state
**Testing**: pytest via `uv run pytest tests/`; ruff via
`uv run ruff check custom_components/ tests/`; pre-commit hooks for mypy,
interrogate, reuse, yamllint, actionlint, and gitlint
**Target Platform**: Home Assistant custom integration on Linux, Docker, and
HA OS, using the HA asyncio event loop
**Project Type**: Single Home Assistant custom integration (HACS)
**Performance Goals**: One O(max_events + max_slots) desired-plan pass per
refresh; no blocking event-loop I/O; all Keymaster service calls remain async
and finish within the configured refresh cadence
**Constraints**: Door-code management is safety-sensitive; RC-managed slots
must not be double-assigned; slots are not reusable until physical clear is
confirmed; reservations are whole-unit and non-overlapping; state-change
callbacks must not re-enter reconciliation
**Scale/Scope**: Typical default 5 managed slots and up to the configured
calendar horizon; design changes span coordinator refresh, EventOverrides,
calendar sensors, utilities, diagnostics, persistence, and tests

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I: Code Quality & Testing | PASS | Implementation must introduce typed, documented reconciliation and persistence helpers plus unit/integration coverage for every invariant in FR-001 through FR-018. |
| II: Atomic Commit Discipline | PASS | Future code should be split into atomic commits: data model/storage, desired-plan computation, apply-diff/confirmed-clear, sensor read-only conversion, diagnostics, and tests. |
| III: Licensing & Attribution | PASS | New planning artifacts include SPDX headers; future new Python modules must include project SPDX headers. |
| IV: Pre-Commit Integrity | PASS | Hooks must not be bypassed; quickstart defines targeted pytest and ruff commands before implementation merge. |
| V: Agent Co-Authorship & DCO | PASS | PLAN and future implementation commits require `git commit -s` and the appropriate `Co-authored-by` trailer. |
| VI: User Experience Consistency | PASS | Existing entities and semantics are preserved; `event_N` sensors keep their attributes but read slot assignment from the desired plan. |
| VII: Performance Requirements | PASS | The desired-plan pass is bounded by configured slots/events and uses HA Store only during setup and state changes, not blocking the event loop. |

**Gate result: PASS** — no violations. Proceeding to Phase 0.

## Project Structure

### Documentation (this feature)

```text
specs/012-slot-reconciliation/
├── plan.md                    # This file
├── research.md                # Phase 0 design decisions and alternatives
├── data-model.md              # Phase 1 entities, schema, and state machine
├── quickstart.md              # Phase 1 validation guide
├── checklists/
│   └── requirements.md        # Existing SPEC-stage checklist
└── tasks.md                   # Phase 2 output only; not created in PLAN stage
```

`contracts/` is intentionally omitted. This feature adds no external HTTP,
service, entity-service, WebSocket, or public API contract. The internal
interfaces are captured in [data-model.md](data-model.md) instead.

### Source Code (repository root)

```text
custom_components/rental_control/
├── coordinator.py             # Load/save Store; run one refresh-level
│                              # reconciliation after calendar parsing and
│                              # before coordinator data is published
├── event_overrides.py         # Keep lock and actual slot cache; replace
│                              # next-slot/greedy APIs with reconcile helpers,
│                              # pending-clear state, duplicate collapse, and
│                              # diagnostics snapshots
├── reconciliation.py          # New pure-ish desired-plan/diff data types and
│                              # deterministic planning helpers
├── util.py                    # Return operation results from set/clear helpers;
│                              # verify clear/set physical state and preserve
│                              # buffer/name/code semantics
├── const.py                   # Add storage key/version and diagnostics/status
│                              # constants as needed
├── sensor.py                  # Continue creating event_N sensors and the
│                              # check-in sensor without entity-name changes
└── sensors/
    ├── calsensor.py           # Remove slot mutation side effects; reflect
    │                          # coordinator desired-plan assignment only
    └── checkinsensor.py       # Expose active checked-in reservation data for
                               # protection; preserve check-in state machine

tests/
├── unit/
│   ├── test_event_overrides.py          # Actual-state, pending-clear,
│   │                                    # duplicate collapse, Store migration
│   ├── test_slot_reconciliation.py      # Desired-plan computation invariants
│   ├── test_util.py                     # Confirmed clear/set verification
│   ├── test_sensors.py                  # event_N read-only reflection
│   └── test_checkin_sensor.py           # Active protection data surface
└── integration/
    ├── test_slot_concurrency.py         # Reconcile atomicity vs callbacks
    └── test_refresh_cycle.py            # End-to-end convergence scenarios
```

**Structure Decision**: Use the existing single-integration layout with one new
internal module, `reconciliation.py`, to keep deterministic planning functions
testable without Home Assistant service-call side effects. `EventOverrides`
continues to own mutable slot state and the existing lock so state-change
callbacks and coordinator reconciliation serialize through the same object.

## Phase 0 Research

Research is complete in [research.md](research.md). It resolves the core design
choices for reconciliation architecture, reservation identity, HA Store
persistence and migration, desired-plan computation, confirmed-clear apply-diff,
manual-edit correction, concurrency, and compatibility/rollout risk.

## Phase 1 Design Artifacts

- [research.md](research.md): required and complete.
- [data-model.md](data-model.md): required and complete.
- [quickstart.md](quickstart.md): required and complete.
- `contracts/`: omitted because no external contract is introduced.
- `update-agent-context.sh`: intentionally not run. HA Store is part of Home
  Assistant and no genuinely new technology, runtime, or dependency is added.

## Post-Design Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I: Code Quality & Testing | PASS | Quickstart defines targeted tests for soonest-N overflow, active protection, corrupt-state convergence, clear-failure safety, persistence, feed-miss tolerance, diagnostics, and preserved semantics. |
| II: Atomic Commit Discipline | PASS | PLAN artifacts are one docs-only commit; future implementation can be decomposed by the source tree above. |
| III: Licensing & Attribution | PASS | `plan.md`, `research.md`, `data-model.md`, and `quickstart.md` include SPDX headers. |
| IV: Pre-Commit Integrity | PASS | No bypasses planned; docs PR must pass repository hooks and CI. |
| V: Agent Co-Authorship & DCO | PASS | PLAN commit uses the required sign-off and co-authorship trailer. |
| VI: User Experience Consistency | PASS | The design keeps existing entity names, event sensor attributes, trimming, buffers, PMS time handling, code regeneration, and check-in tracking semantics. |
| VII: Performance Requirements | PASS | Planning is linear in configured events and slots; HA Store writes are debounced to mapping/status changes rather than every callback. |

**Gate result: PASS** — no complexity violations.

## Complexity Tracking

> No violations to justify — all constitution gates pass.

## Phase Notes

- PLAN stage owns only design artifacts. Do not create `tasks.md` or modify
  source code in this PR.
- Implementation must preserve the existing Keymaster-managed slot range and
  must never touch unmanaged slots.
- The first implementation task should build the pure desired-plan model and
  tests before wiring HA service calls.
