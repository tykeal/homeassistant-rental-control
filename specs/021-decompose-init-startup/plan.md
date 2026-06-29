<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Implementation Plan: Decompose Init Startup Readability

**Feature**: `021-decompose-init-startup` | **Planning Branch**:
`021-decompose-init-startup-plan` | **Date**: 2026-06-29 | **Spec**:
[spec.md](spec.md)
**Input**: Feature specification from
`specs/021-decompose-init-startup/spec.md` and GitHub issue #658

## Summary

Decompose `custom_components/rental_control/__init__.py` without changing
Home Assistant-visible behavior. The current over-threshold package entry
module owns setup, unload, update-listener orchestration, normal Keymaster
state listeners,
package-level re-exports from #572, and the startup Keymaster readability
watcher. The remaining complexity offender is
`async_arm_startup_readability_refresh`, a 143-line one-shot watcher arming
function with nested cleanup, debounce, refresh, and watchdog callbacks.

The implementation will keep `__init__.py` as the Home Assistant entry-point
shell for `async_setup_entry`, `async_unload_entry`, `update_listener`, and
`async_start_listener`. Startup readability discovery, readable-state checks,
one-shot arming, debounce scheduling, watchdog expiry, refresh task cleanup, and
unload cleanup will move to a new sibling module,
`custom_components/rental_control/startup_readability.py`. The package module
will re-export `async_arm_startup_readability_refresh` so existing tests and
hidden callers can still import it from `custom_components.rental_control`, and
`async_start_listener` will remain patchable at that same package path.

## Technical Context

**Language/Version**: Python >=3.14.2
**Primary Dependencies**: Home Assistant runtime >=2026.4.0 per `hacs.json`,
including config-entry lifecycle, state tracking, `async_call_later`, and
`async_track_state_change_event`; dev/test dependency `homeassistant>=2026.6.0`
per `pyproject.toml`; test tooling `pytest-homeassistant-custom-component` and
`aioresponses`; declared libraries `icalendar>=7.0.0` and
`x-wr-timezone>=2.0.0`
**Storage**: No new storage. The refactor preserves existing config-entry data,
coordinator-owned slot store, `hass.data[DOMAIN][entry_id]`, and the
`UNSUB_LISTENERS` cleanup list.
**Testing**: Existing oracle `uv run pytest tests/unit/test_init.py`; broader
caller coverage through
`uv run pytest tests/integration/test_refresh_cycle.py tests/integration/test_full_setup.py`;
ruff via `uv run ruff check custom_components/ tests/`; pre-commit hooks for
reuse, ruff, mypy, interrogate, yamllint, actionlint, and gitlint
**Target Platform**: Home Assistant custom integration on the HA asyncio event
loop for Linux, HA OS, Docker, and HACS-managed installs
**Project Type**: Single Home Assistant custom integration
**Performance Goals**: Startup readability processing performs the same
in-memory entity ID discovery, state checks, state-change subscription, debounce
callback, watchdog callback, and at most one corrective coordinator refresh with
no extra state writes, config-entry writes, service calls, blocking I/O, or
user-visible delay beyond the existing debounce/watchdog behavior
**Constraints**: Documentation-only PLAN PR; no production code. Runtime
implementation must preserve setup/unload/update-listener behavior, first-refresh
ordering, startup unreadability capture, direct package import of
`async_arm_startup_readability_refresh`, package-path patching of
`async_start_listener`, package-level migration and listener re-exports from
#572, exact debounce delay, watchdog interval, one-shot guarantee, cancellation
order, refresh error handling, and no Aislop suppression directives.
**Scale/Scope**: One over-threshold entry module becomes a focused Home
Assistant shell plus one startup-readability helper module. Current measured
debt is file size and `async_arm_startup_readability_refresh` at 143 lines. Implementation
target: every init/startup-readability file below 400 lines, every project-owned
function below 80 lines, and every project-owned parameter list no more than six
parameters.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I: Code Quality & Testing | PASS | Plan requires existing `test_init.py` startup, unload, reload, and update-listener tests to pass unchanged, plus focused helper parity tests where useful. |
| II: Atomic Commit Discipline | PASS | This PR is one docs-only PLAN commit. Future implementation can split module extraction, watcher object introduction, shell re-export wiring, and tests into atomic commits. |
| III: Licensing & Attribution | PASS | New markdown artifacts include SPDX headers. Future `startup_readability.py` must include project SPDX headers, type hints, and docstrings. |
| IV: Pre-Commit Integrity | PASS | No hook bypass is planned. Quickstart defines validation before any implementation merge. |
| V: Agent Co-Authorship & DCO | PASS | The PLAN commit uses `git commit -s` and the requested Claude co-author trailer. |
| VI: User Experience Consistency | PASS | HA lifecycle order, package-level imports, patch seams, listener cleanup, and startup refresh behavior are explicitly preserved. |
| VII: Performance Requirements | PASS | Extracted logic keeps the same callbacks and one-shot refresh behavior with no extra I/O, writes, tasks, or delays. |

**Gate result: PASS** — no violations. Proceeding to Phase 0.

## Project Structure

### Documentation (this feature)

```text
specs/021-decompose-init-startup/
├── plan.md                    # This file
├── research.md                # Phase 0 decisions and alternatives
├── data-model.md              # Phase 1 watcher entities and ownership
├── quickstart.md              # Phase 1 parity validation guide
└── tasks.md                   # Phase 2 output only; not created in PLAN stage
```

`contracts/` is intentionally omitted. This behavior-preserving refactor adds no
external HTTP API, WebSocket API, Home Assistant service, entity-service, event
payload, storage schema, or new public Python API contract. Internal watcher
state and helper boundaries are specified in this plan and in
[data-model.md](data-model.md).

### Source Code (repository root)

```text
custom_components/rental_control/
├── __init__.py                         # Public HA entry shell; setup, unload,
│                                       # update_listener, async_start_listener,
│                                       # migrations/listener re-exports, and
│                                       # startup-readability re-export remain
├── startup_readability.py              # Managed slot entity discovery,
│                                       # readable-state checks, watcher object,
│                                       # debounce/watchdog/refresh cleanup
├── migrations.py                       # Existing #572 migration extraction,
│                                       # unchanged except package re-export use
├── listeners.py                        # Existing #572 keymaster event listener,
│                                       # unchanged except package re-export use
├── coordinator.py                      # Existing coordinator contract reused
└── util.py                             # Existing get_entry_data helper reused

tests/
├── unit/
│   ├── test_init.py                    # Existing behavior oracle unchanged
│   └── test_startup_readability.py     # Optional focused helper parity tests
└── integration/
    ├── test_full_setup.py              # Existing setup caller smoke coverage
    └── test_refresh_cycle.py           # Existing startup/reconciliation coverage
```

**Structure Decision**: Keep the integration package entry file in
`__init__.py` rather than moving lifecycle functions to a different module.
Home Assistant imports the package directly for `async_setup_entry`,
`async_unload_entry`, `async_migrate_entry`, and update-listener behavior.
Visible tests import `async_arm_startup_readability_refresh` from
`custom_components.rental_control` and patch
`custom_components.rental_control.async_start_listener`. A sibling
`startup_readability.py` mirrors the #572 pattern used for `migrations.py` and
`listeners.py`: move a focused concern to a sibling module, then preserve the
package-level compatibility name with an import/re-export.

## Concrete Decomposition Design

### Public compatibility boundary

`custom_components.rental_control` remains the package-level Home
Assistant-facing module. These names stay importable and effective at the
current package path:

- `async_setup_entry`
- `async_unload_entry`
- `update_listener`
- `async_start_listener`
- `async_migrate_entry`
- `async_register_keymaster_listener`
- `async_arm_startup_readability_refresh`

`async_setup_entry` still captures startup unreadability after
`coordinator.async_setup_keymaster_overrides()` and before the first coordinator
refresh, stores the coordinator in `hass.data`, performs
`async_config_entry_first_refresh()`, arms startup readability refresh, starts
normal listeners, forwards platforms, registers the keymaster event listener,
adds the update listener, and performs generated-file cleanup in the same order.

`update_listener` continues to call the package module global
`async_start_listener` at runtime. The implementation must not move the normal
state listener restart behind an imported helper in a way that bypasses patches
to `custom_components.rental_control.async_start_listener`.

### Ground-truth source and test analysis

The implementation must start from live `origin/main`, not the issue summary
alone. Current source facts captured during planning:

- `__init__.py` is above the active 400-line threshold.
- `async_setup_entry` calls `_needs_startup_readability_refresh` around current
  line 97 and calls `async_arm_startup_readability_refresh` around line 113.
- The startup-readability concern includes
  `_managed_slot_readability_entity_ids`, `_is_readable_keymaster_state`,
  `_all_managed_slots_readable`, `_needs_startup_readability_refresh`, and
  `async_arm_startup_readability_refresh`.
- `async_arm_startup_readability_refresh` is 143 lines with four parameters and
  nested callbacks `_remove_listener_reference`, `_cancel_watchers`,
  `_remove_self`, `_refresh_done`, `_async_refresh_once`,
  `_refresh_if_readable`, `_schedule_refresh`, and `_expire`.
- There is no Aislop directive in `__init__.py`; the implementation must add no
  complexity suppression.
- Visible tests import `async_arm_startup_readability_refresh` from the package
  at `tests/unit/test_init.py:23`, call it directly around line 201, and patch
  `custom_components.rental_control.async_start_listener` around lines 364 and
  469.
- The previous #572 extractions used sibling modules plus package re-exports:
  `migrations.py` for `async_migrate_entry` and `listeners.py` for
  `async_register_keymaster_listener`.

### Entry shell responsibilities

`__init__.py` keeps responsibilities that require the Home Assistant package
entry point or package-path compatibility:

1. `async_setup_entry()` setup orchestration and first-refresh ordering;
2. `async_unload_entry()` platform unload, generated-file cleanup, reload,
   listener cleanup, domain-data removal, and notification dismissal;
3. `update_listener()` option mutation, coordinator update, listener cleanup,
   package-global `async_start_listener` restart, and keymaster listener
   registration;
4. `async_start_listener()` normal Keymaster state-change tracking and current
   package-path patch seam;
5. package-level imports/re-exports for `async_migrate_entry`,
   `async_register_keymaster_listener`, and
   `async_arm_startup_readability_refresh`.

The shell imports the startup-readability helpers it needs from
`startup_readability.py`, but runtime tests and hidden callers continue to import
and call the public arming function from the package module.

### `startup_readability.py`

`startup_readability.py` owns the complete startup-readability concern:

- constants `_STARTUP_READABILITY_REFRESH_DELAY = 1.5` and
  `_STARTUP_READABILITY_WATCHDOG = 10 * 60`;
- `_managed_slot_readability_entity_ids(coordinator)` returning text/switch
  entity IDs for every managed slot;
- `_is_readable_keymaster_state(state)` treating `None` and `unavailable` as
  unreadable while preserving `unknown` and normal states as readable;
- `_all_managed_slots_readable(hass, entity_ids)` using the same state lookup;
- `_needs_startup_readability_refresh(hass, coordinator)` returning the current
  `(needs_refresh, entity_ids)` tuple;
- `async_arm_startup_readability_refresh(...)` as a short public orchestrator;
- a small watcher object or focused module-level helpers for lifecycle state.

The module must import Home Assistant event helpers and project constants
directly, not through `__init__.py`, to avoid circular imports. The package
module may import the public arming function and private needs helper from this
module.

### Watcher object decomposition

Preferred implementation introduces a private dataclass or class, for example
`_StartupReadabilityWatcher`, with slots for:

- `hass`
- `config_entry`
- `coordinator`
- `entity_ids`
- `done`
- `unsub_state`
- `unsub_timer`
- `unsub_watchdog`
- `refresh_task`

The public `async_arm_startup_readability_refresh` becomes a thin function below
80 lines:

1. call `_needs_startup_readability_refresh(hass, coordinator)`;
2. return when neither current state nor startup state needs a refresh;
3. instantiate `_StartupReadabilityWatcher` with the computed entity IDs;
4. call `watcher.arm()`.

The watcher owns short methods corresponding to today's nested callbacks:

- `arm()`: subscribe to state changes, start the watchdog, append
  `remove_self` to `UNSUB_LISTENERS`, schedule an immediate debounce when all
  entities are already readable, and log the same armed message.
- `remove_listener_reference()`: remove `remove_self` from the entry cleanup
  list when present, returning safely if entry data disappeared.
- `cancel_watchers()`: cancel debounce timer, watchdog, and state subscription
  in the current timer/watchdog/state order, setting handles to `None`.
- `remove_self()`: mark done, cancel watchers, cancel any pending refresh task,
  clear the task reference, and remove the cleanup reference.
- `refresh_done(task)`: clear the refresh task and remove the cleanup reference
  after the one-shot task finishes.
- `async_refresh_once()`: skip safely if entry data disappeared, otherwise call
  `coordinator.async_refresh()` and log exceptions without propagating them.
- `refresh_if_readable(now)`: clear the debounce handle, return if done or not
  all readable, then mark done, cancel watchers, create the same named refresh
  task, and attach `refresh_done`.
- `schedule_refresh(event)`: ignore already done watchers, unreadable new states,
  and readable-to-readable transitions; otherwise cancel any existing debounce
  timer and schedule `refresh_if_readable` after the same delay.
- `expire(now)`: log the same expiration message and call `remove_self()`.

This maps each current nested closure to a focused helper while preserving the
observable one-shot, debounce, watchdog, and cleanup semantics.

### One-shot and cleanup invariants

The implementation must preserve these sequencing details exactly:

1. `startup_slots_unreadable=True` arms the watcher even if current state is now
   readable, preserving missed-transition coverage.
2. The cleanup callback is appended to
   `hass.data[DOMAIN][entry_id][UNSUB_LISTENERS]` after state and watchdog
   handles are created, matching current listener-list behavior.
3. A readable transition only schedules debounce when the new state is readable
   and the old state was missing or unreadable.
4. Multiple readable transitions cancel and replace the pending debounce timer.
5. `refresh_if_readable` sets the debounce handle to `None` before returning,
   checks all watched entities, and only then flips `done` and creates the task.
6. `done` prevents additional debounce or refresh scheduling after success,
   unload, or watchdog expiry.
7. `remove_self` cancels a pending refresh task only when it exists and is not
   done, then clears the reference.
8. `refresh_done` removes the unload cleanup reference after the task completes,
   so successful one-shot refreshes leave no stale cleanup callback.
9. Watchdog expiry remains non-fatal and only removes the watcher.
10. Missing entry data and refresh exceptions remain safe, logged, and
    non-propagating.

### Compatibility wiring

`__init__.py` should import and re-export startup readability similarly to the
existing #572 pattern:

```python
from .startup_readability import (
    _needs_startup_readability_refresh as _needs_startup_readability_refresh,
)
from .startup_readability import (
    async_arm_startup_readability_refresh as async_arm_startup_readability_refresh,
)
```

A direct private import for `_needs_startup_readability_refresh` is acceptable
because `async_setup_entry` needs the startup unreadability tuple before the
first refresh. The public import keeps this current test import valid:

```python
from custom_components.rental_control import async_arm_startup_readability_refresh
```

No compatibility wrapper should capture `async_start_listener` away from the
package global. `update_listener` must continue to resolve the package-level
name so this current patch path remains effective:

```text
custom_components.rental_control.async_start_listener
```

## Phase 0 Research Output

See [research.md](research.md). All planning questions are resolved; no open
clarifications remain.

## Phase 1 Design Output

See [data-model.md](data-model.md) for the internal watcher entities and
[quickstart.md](quickstart.md) for the implementation validation guide. No
contracts are generated because this refactor introduces no external API,
service, event, entity, or changed public caller behavior. Agent-context updates
are intentionally omitted because no new language, framework, runtime
dependency, package manager, or tool is introduced.

## Post-Design Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I: Code Quality & Testing | PASS | Design keeps entry-point wrappers and package-level imports testable, preserves existing `test_init.py`, and adds optional focused helper parity tests. |
| II: Atomic Commit Discipline | PASS | PLAN artifacts are one docs-only change; future implementation can be split by extraction, watcher decomposition, wiring, and tests. |
| III: Licensing & Attribution | PASS | `plan.md`, `research.md`, `data-model.md`, and `quickstart.md` include SPDX headers. Future Python helper files must do the same. |
| IV: Pre-Commit Integrity | PASS | The PR must pass hooks and CI without bypass flags. |
| V: Agent Co-Authorship & DCO | PASS | The planned commit uses sign-off and the requested Claude co-author trailer. |
| VI: User Experience Consistency | PASS | Setup/unload/update order, package imports, `async_start_listener` patching, one-shot refresh, debounce, watchdog, and cleanup semantics are preserved. |
| VII: Performance Requirements | PASS | The watcher object performs the same in-memory checks and existing one-shot task only, with no new I/O, writes, services, or delays. |

**Gate result: PASS** — no plan-stage constitution violations.

## Complexity Tracking

No constitutional violations require justification. Existing `__init__.py` and
startup-readability complexity debt remains the implementation target. If
`startup_readability.py` approaches 400 lines or any watcher helper approaches
80 lines during implementation, split by coherent lifecycle concern instead of
adding any `aislop-ignore`, `aislop-ignore-file`, or equivalent suppression.

## Phase Notes

- PLAN stage stops here. Do not create `tasks.md` or modify production code in
  this PR.
- Implementation must treat current `origin/main` `__init__.py` as truth.
  Planning shorthand and issue text are secondary when they disagree with
  source.
- Keep the refactor behavior-preserving. Any discovered behavior bug or business
  rule improvement belongs in a separate issue/feature, not this decomposition.
