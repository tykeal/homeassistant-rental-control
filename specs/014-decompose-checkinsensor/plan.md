<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Implementation Plan: Decompose Check-in Sensor

**Feature**: `014-decompose-checkinsensor` | **Planning Branch**:
`014-decompose-checkinsensor-plan` | **Date**: 2026-06-26 | **Spec**:
[spec.md](spec.md)
**Input**: Feature specification from
`specs/014-decompose-checkinsensor/spec.md` and GitHub issue #577

## Summary

Decompose `custom_components/rental_control/sensors/checkinsensor.py` without
changing Home Assistant-visible behavior. The current source is the load-bearing
contract: it contains the four states (`no_reservation`, `awaiting_checkin`,
`checked_in`, `checked_out`), a 229-line coordinator update handler, a 208-line
restore validator, a wide `CheckinExtraStoredData` initializer, a single timer
unsubscribe handle backed by `async_track_point_in_time()`, manual checkout,
debug state override, and Keymaster unlock handling.

The implementation will keep `CheckinTrackingSensor` as the HA-facing entity
boundary while extracting pure decision logic, restore reconciliation,
persistence snapshots, event selection, and timer scheduling into focused
internal modules under `custom_components/rental_control/sensors/checkin/`.
The entity shell will translate coordinator data into snapshots, apply ordered
decisions, fire existing HA events, call `async_write_ha_state()` at the same
points, and continue registering the same service-facing methods.

## Technical Context

**Language/Version**: Python >=3.14.2
**Primary Dependencies**: Home Assistant runtime >=2026.4.0 per `hacs.json`;
dev/test dependency `homeassistant>=2026.6.0` per `pyproject.toml`;
`pytest-homeassistant-custom-component`, `icalendar>=7.0.0`, and
`x-wr-timezone>=2.0.0`
**Storage**: Home Assistant `RestoreEntity` extra data through
`CheckinExtraStoredData`; no new persistent storage
**Testing**: `uv run pytest tests/`; targeted check-in tests in
`tests/unit/test_checkin_sensor.py` and
`tests/integration/test_checkin_tracking.py`; ruff via
`uv run ruff check custom_components/ tests/`; pre-commit hooks for reuse,
ruff, mypy, interrogate, yamllint, actionlint, and gitlint
**Target Platform**: Home Assistant custom integration on the HA asyncio event
loop for Linux, HA OS, Docker, and HACS-managed installs
**Project Type**: Single Home Assistant custom integration
**Performance Goals**: Coordinator-update path remains O(events) over the
already-available coordinator data, performs no blocking I/O, performs no new
coordinator refreshes, and keeps the same HA state-write count/order
**Constraints**: Documentation-only PLAN PR; no production code. Runtime
refactor must preserve native state, attributes, event payloads, warnings,
self-healing, Keymaster monitoring behavior, debug override clearing, and timer
cancellation/target semantics exactly.
**Scale/Scope**: One roughly 1,700-line sensor module is split into an entity
shell and internal helper modules. The implementation target is files below 400
lines, functions below 80 lines, and project-owned initializers no more than
six parameters.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I: Code Quality & Testing | PASS | Plan requires existing check-in unit/integration tests to pass unchanged and adds focused pure-decision tests for extracted logic. |
| II: Atomic Commit Discipline | PASS | This PR is one docs-only PLAN commit. Future implementation can split extraction, wiring, and tests into atomic commits. |
| III: Licensing & Attribution | PASS | New markdown artifacts include SPDX headers. Future Python modules must retain project SPDX headers. |
| IV: Pre-Commit Integrity | PASS | No hook bypass is planned. Quickstart defines local validation before implementation merge. |
| V: Agent Co-Authorship & DCO | PASS | The PLAN commit uses `git commit -s` and the appropriate AI co-author trailer. |
| VI: User Experience Consistency | PASS | The design preserves entity state names, attributes, event names/payloads, services, and timing rules. |
| VII: Performance Requirements | PASS | Hot-path delegation is pure in-memory work over existing coordinator data and does not add I/O or refreshes. |

**Gate result: PASS** — no violations. Proceeding to Phase 0.

## Project Structure

### Documentation (this feature)

```text
specs/014-decompose-checkinsensor/
├── plan.md                    # This file
├── research.md                # Phase 0 decisions and alternatives
├── data-model.md              # Phase 1 entities and decision models
├── quickstart.md              # Phase 1 parity validation guide
└── tasks.md                   # Phase 2 output only; not created in PLAN stage
```

`contracts/` is intentionally omitted. This refactor introduces no external
HTTP, WebSocket, Home Assistant service, entity-service, event, or public API
contract. Internal decision interfaces are specified in
[data-model.md](data-model.md).

### Source Code (repository root)

```text
custom_components/rental_control/sensors/
├── checkinsensor.py              # HA entity shell, compatibility imports,
│                                 # lifecycle hooks, services, event bus,
│                                 # coordinator callback, and state writes
└── checkin/
    ├── __init__.py               # Internal package marker and typed exports
    ├── models.py                 # CheckinStateSnapshot,
    │                             # CoordinatorUpdateContext,
    │                             # TransitionDecision,
    │                             # RestoreReconciliationDecision,
    │                             # ScheduledTransition, enums, value types
    ├── persistence.py            # CheckinExtraStoredData and
    │                             # snapshot <-> stored-dict conversion
    ├── event_selection.py        # event_key(), relevant/tracked/follow-on
    │                             # event selection, slot-name extraction
    ├── transition_decisions.py   # coordinator-update decision functions,
    │                             # split by current check-in state
    ├── restore_decisions.py      # restore reconciliation decision functions,
    │                             # split by restored state
    ├── timers.py                 # CheckinTimerManager wrapping
    │                             # async_track_point_in_time handles
    └── applicator.py             # ordered decision application helpers that
                                  # call entity transition methods/effects

tests/
├── unit/
│   ├── test_checkin_sensor.py        # Existing behavior tests pass unchanged;
│   │                                 # add shell/applicator compatibility cases
│   ├── test_checkin_decisions.py     # New pure coordinator-transition tests
│   ├── test_checkin_restore.py       # New pure restore-reconciliation tests
│   ├── test_checkin_timers.py        # New timer cancel/replace tests
│   └── test_checkin_persistence.py   # New stored-data round-trip tests
└── integration/
    └── test_checkin_tracking.py      # Existing timer/lifecycle parity tests
```

**Structure Decision**: Keep the existing Home Assistant integration layout and
create an internal `sensors.checkin` subpackage for the decomposed behavior.
Leave `CheckinTrackingSensor` importable from `sensors.checkinsensor` so the HA
platform setup (`custom_components/rental_control/sensor.py:68-72`) and tests
continue constructing it the same way. Re-export `CheckinExtraStoredData` from
`checkinsensor.py` during the move so current internal imports and tests do not
need behavior changes while the persistence implementation lives in
`checkin/persistence.py`.

## Concrete Decomposition Design

### Entity shell responsibilities

`checkinsensor.py` remains the only HA-facing boundary required by FR-010. It
keeps:

- `CheckinTrackingSensor.__init__(hass, coordinator, config_entry)`. Ground
  truth on main is already three project-owned parameters, so no caller-facing
  collapse is needed for the entity constructor.
- HA entity properties: `unique_id`, `state`, `icon`, `device_info`, and
  `extra_state_attributes`.
- `extra_restore_state_data`, `async_added_to_hass`,
  `async_will_remove_from_hass`, `async_checkout`, `async_set_state`,
  `async_handle_keymaster_unlock`, and `_async_update_lock_code_expiry`.
- HA side effects: bus events, service calls, `async_write_ha_state()`, and
  coordinator subscription callbacks.

The shell also owns a small compatibility layer for private transition methods
that existing tests exercise directly (`_transition_to_awaiting`,
`_transition_to_checked_in`, `_transition_to_checked_out`,
`_transition_to_no_reservation`). Those methods may delegate to extracted
applicator helpers, but their side-effect order must remain unchanged.

### Initializer collapse plan

The live entity initializer has three parameters. The oversized initializer is
`CheckinExtraStoredData.__init__`, which currently accepts the full persisted
field list and is called from `extra_restore_state_data`, `from_dict`, and one
direct test fixture in `tests/unit/test_checkin_sensor.py:3269-3281`.

Implementation will introduce `CheckinStateSnapshot` in `models.py` for new
internal call sites. `CheckinExtraStoredData` will keep the current keyword-field
construction as a compatibility shim, for example
`__init__(self, snapshot: CheckinStateSnapshot | None = None, **legacy_fields:
Any)`, while adding `from_snapshot()` for new code. `from_dict()` will still
accept the same dictionary shape and return an object whose `as_dict()` emits the
same keys, ISO datetime strings, defaults, and optional fields. Existing
check-in tests that instantiate `CheckinExtraStoredData(...)` with the current
keywords must pass unchanged; no HA entity contract or platform setup caller
changes are required.

If implementation discovers a pre-existing branch with a wider entity
constructor, it should use a `CheckinSensorConfig` dataclass containing stable HA
runtime dependencies and still keep the live platform call site to at most
`hass`, `coordinator`, `config_entry`, and `config` while preserving entity
setup semantics. That fallback is not needed for current `origin/main`.

### Coordinator-update split

`_handle_coordinator_update()` becomes a short orchestration method:

1. log the existing debug message;
2. if `last_update_success` is false, call `async_write_ha_state()` and return;
3. create a `CheckinStateSnapshot` and `CoordinatorUpdateContext` from current
   in-memory fields, coordinator data, current time, cleaning-window provider,
   event-prefix provider, and Keymaster monitoring status;
4. call `transition_decisions.decide_coordinator_update()`;
5. apply the returned ordered decision effects; and
6. write HA state only where the existing path writes it.

`transition_decisions.py` splits the current branches into small functions:

- `decide_no_reservation_update()` chooses relevant event -> awaiting or
  write-only.
- `decide_awaiting_update()` handles tracked-event refresh, earlier relevant
  event replacement, automatic check-in when monitoring is off, cancelled event
  fallback, and no-event reset.
- `decide_checked_in_update()` handles tracked event refresh, far-future
  self-healing, ended-event safety checkout, changed-end rescheduling,
  transient missing-event preservation, missing-end reset, and warning/debug
  level choice.
- `decide_checked_out_update()` handles follow-on discovery, same follow-on
  no-op/write, changed follow-on recompute, removed follow-on recompute, and
  missing timer recompute.

Each function returns a `TransitionDecision` containing ordered
`DecisionEffect` records rather than mutating HA state. Multi-step behavior such
as far-future self-healing remains ordered as it is today: checkout first, then
possibly transition to a different awaiting event, then return without an extra
write. Applicator helpers preserve that sequence by invoking entity-owned
`CheckinTrackingSensor` transition/write methods; extracted helper modules must
not call HA bus, service, or `async_write_ha_state()` APIs directly.

### Restore reconciliation split

`_validate_restored_state()` becomes a wrapper around
`restore_decisions.decide_restore_state()` and an applicator for silent restore
effects. The current state branches split into:

- `decide_restore_checked_in()` for far-future self-healing, ended-event silent
  checkout, valid auto-checkout reschedule, and unknown end handling.
- `decide_restore_awaiting()` for silent automatic check-in when monitoring is
  off, immediate silent checkout when the restored stay also ended, valid
  auto-check-in reschedule, and missing start cleanup.
- `decide_restore_checked_out()` for new-event handoff, expired linger reset,
  and linger recomputation from restored checkout/target data.
- `decide_restore_no_reservation()` for pending FR-006c follow-up timer
  recreation or stale follow-up cleanup.
- `decide_restore_unknown()` for the existing warning and reset path.

Restore applicator effects must remain silent: restore catch-up continues to
avoid firing check-in/checkout bus events and only writes HA state where the
current source writes it.

Restore wiring must also preserve `async_added_to_hass()`'s current second pass:
after restored data is loaded and restore validation/applicator effects finish,
and also in the no-prior-state path, run the coordinator-update orchestration
when `coordinator.last_update_success` and `coordinator.data is not None`. This
second pass is not restore-silent; it preserves current event re-selection,
timer replacement, side-effect ordering, and state-write behavior from
`checkinsensor.py:1318-1337`.

### Persistence compatibility

`persistence.py` keeps the existing stored dictionary contract exactly:

- keys remain `state`, `tracked_event_summary`, `tracked_event_start`,
  `tracked_event_end`, `tracked_event_slot_name`, `checkin_source`,
  `checkout_source`, `checkout_time`, `transition_target_time`,
  `checked_out_event_key`, `next_event_start_day`, and `checkin_lock_name`;
- datetime values remain ISO strings or `None` and parse through
  `dt_util.parse_datetime()` with the same warnings on invalid values;
- missing `state` defaults to `no_reservation` and missing optional fields stay
  `None`;
- no schema migration, field rename, or manual state deletion is introduced.

### Timer semantics

The current file uses `async_track_point_in_time()` for five callback paths and
a single `_unsub_timer` handle: auto-check-in, auto-checkout, same-day
linger-to-awaiting, linger-to-no-reservation, and FR-006c
no-reservation-to-awaiting follow-up. No `async_call_later()` use exists in the
source. `CheckinTimerManager` will preserve that
single-active-scheduled-transition model while making cancellation testable:

- every replacement calls the prior unsubscribe before storing the new handle;
- callbacks set the handle to `None` before guard checks;
- callbacks keep current state guards so stale callbacks cannot trigger new
  transitions;
- transition target and follow-up day fields are updated in the same order as
  today;
- `async_will_remove_from_hass`, debug `async_set_state`, and all state-exit
  paths still cancel pending timers.

Focused timer tests should assert cancel-before-replace, handle clearing after
fire, no duplicate active handle for auto-check-in/auto-checkout/linger/follow-up
paths, and unchanged targets for same-day midpoint, different-day midnight,
cleaning-window, and restored follow-up timers.

### Hot-path safeguards

Coordinator-update delegation is pure Python over the current snapshot and
`coordinator.data`. It must not call Home Assistant services, perform file or
network I/O, request coordinator refreshes, await coroutines, or add state
writes. Event lookup remains a linear scan over the already-fetched event list,
matching the current `_get_relevant_event()`, `_find_tracked_event()`, and
`_find_followon_event()` behavior.

## Phase 0 Research

Research is complete in [research.md](research.md). It records the module split,
snapshot/stored-data initializer approach, ordered decision/applicator pattern,
timer manager, and compatibility boundaries, with alternatives grounded in the
current source.

## Phase 1 Design Artifacts

- [research.md](research.md): required and complete.
- [data-model.md](data-model.md): required and complete.
- [quickstart.md](quickstart.md): required and complete.
- `contracts/`: omitted because no external API, entity service, or event
  contract is introduced or changed.
- `update-agent-context.sh`: intentionally not run. The plan adds no new
  language, framework, database, runtime, package manager, or agent-relevant
  technology beyond the Python/Home Assistant stack already documented in the
  repository.

## Post-Design Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I: Code Quality & Testing | PASS | Quickstart requires existing check-in behavior tests plus pure decision, restore, timer, persistence, debug override, and Keymaster unlock regression tests. |
| II: Atomic Commit Discipline | PASS | PLAN artifacts are one docs-only change; implementation can be split into extraction, wiring, and test commits. |
| III: Licensing & Attribution | PASS | `plan.md`, `research.md`, `data-model.md`, and `quickstart.md` include SPDX headers. |
| IV: Pre-Commit Integrity | PASS | The PR must pass hooks and CI without bypass flags. |
| V: Agent Co-Authorship & DCO | PASS | The planned commit uses sign-off and the appropriate AI co-author trailer. |
| VI: User Experience Consistency | PASS | State names, attributes, bus events, services, persistence keys, and timing rules are explicitly preserved. |
| VII: Performance Requirements | PASS | The design keeps coordinator-update work in memory, linear, synchronous, and side-effect-equivalent. |

**Gate result: PASS** — no complexity violations.

## Complexity Tracking

> No violations to justify — all constitution gates pass.

## Phase Notes

- PLAN stage stops here. Do not create `tasks.md` or modify production code in
  this PR.
- Implementation must validate every extracted decision against the real source
  behavior and existing tests before deleting compatibility shims.
- Because current source differs from the issue shorthand, implementation must
  treat current `origin/main` as truth: entity `__init__` has three parameters;
  the wide initializer to collapse is the persisted-data class.
