<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Implementation Plan: Decompose Util

**Feature**: `018-decompose-util` | **Planning Branch**:
`018-decompose-util-plan` | **Date**: 2026-06-28 | **Spec**:
[spec.md](spec.md)
**Input**: Feature specification from
`specs/018-decompose-util/spec.md` and GitHub issue #578

## Summary

Decompose `custom_components/rental_control/util.py` without changing Home
Assistant-visible behavior. The current 1,173-line module is the compatibility
and monkeypatch contract for Keymaster set, clear, and update-times service
helpers; Keymaster state-change handling; event identity extraction; slot and
calendar naming; text-state normalization; buffer handling; UUID, cleanup,
reload, and service-call helpers.

The implementation will keep `util.py` as the public compatibility shell and add
focused sibling modules for the behavior currently mixed into that file:
`keymaster_services.py` for `async_fire_*`, `state_handlers.py` for
`handle_state_change`, and `helpers.py` for stable generic helper behavior. All
current names imported from `util.py` remain importable from `util.py`. The
monkeypatch-sensitive fire helpers and event identity helpers remain patchable at
`util.*`, while visible `event_overrides.*` and `coordinator.*` patch paths stay
effective through runtime wrappers that call back through the `util` module.

## Technical Context

**Language/Version**: Python >=3.14.2
**Primary Dependencies**: Home Assistant runtime >=2026.4.0 per `hacs.json`;
dev/test dependency `homeassistant>=2026.6.0` per `pyproject.toml`;
`pytest-homeassistant-custom-component`, `icalendar>=7.0.0`, and
`x-wr-timezone>=2.0.0`
**Storage**: N/A; this refactor adds no persistent storage and preserves the
existing coordinator/EventOverrides cache-only state authorities
**Testing**: `uv run pytest tests/`; targeted utility and caller coverage in
`tests/unit/test_util.py`, `tests/unit/test_event_overrides.py`,
`tests/unit/test_event_overrides_apply.py`, `tests/unit/test_coordinator.py`,
`tests/unit/test_coordinator_buffer_update.py`, `tests/unit/test_sensors.py`,
`tests/integration/test_refresh_cycle.py`, and
`tests/integration/test_slot_concurrency.py`; ruff via
`uv run ruff check custom_components/ tests/`; pre-commit hooks for reuse, ruff,
mypy, interrogate, yamllint, actionlint, and gitlint
**Target Platform**: Home Assistant custom integration on the HA asyncio event
loop for Linux, HA OS, Docker, and HACS-managed installs
**Project Type**: Single Home Assistant custom integration
**Performance Goals**: Keymaster service helpers issue the same service calls in
the same order with the same confirmation waits. State-change callbacks keep the
same 0.1-second settle delay and perform no new refresh, Store, or service-call
side effects. Extracted helpers are in-memory over existing inputs.
**Constraints**: Documentation-only PLAN PR; no production code. Runtime
implementation must preserve the full `util.py` import surface, `util.*`,
`event_overrides.*`, and `coordinator.*` monkeypatch seams, Keymaster service
order, state-change callback semantics, text-state/load-bearing self-heal
semantics, `apply_buffer`, and all existing tests unchanged.
**Scale/Scope**: One 1,173-line module becomes a small compatibility shell plus
focused sibling modules. Current measured complexity debt is file size plus
`handle_state_change` (204 lines), `async_fire_set_code` (174),
`async_fire_clear_code` (122), and `async_fire_update_times` (86). No current
function exceeds the six-parameter threshold. Implementation target: every
utility-related file below 400 lines, every project-owned function below 80
lines, and no new complexity `aislop-ignore-file` directive.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I: Code Quality & Testing | PASS | Plan requires existing utility, event override, coordinator, refresh-cycle, slot-concurrency, sensor, calendar, and helper tests unchanged plus focused parity tests for extracted service, state, and helper modules. |
| II: Atomic Commit Discipline | PASS | This PR is one docs-only PLAN commit. Future implementation can split helper extraction, shell wrappers, caller patch-preservation, and tests into atomic commits. |
| III: Licensing & Attribution | PASS | New markdown artifacts include SPDX headers. Future Python modules must include project SPDX headers. |
| IV: Pre-Commit Integrity | PASS | No hook bypass is planned. Quickstart defines local validation before implementation merge. |
| V: Agent Co-Authorship & DCO | PASS | The PLAN commit uses `git commit -s` and the requested AI co-author trailer. |
| VI: User Experience Consistency | PASS | Public imports, service calls, override updates, diagnostics-sensitive behavior, and monkeypatch targets are explicitly preserved. |
| VII: Performance Requirements | PASS | The split keeps HA side effects and waits identical and adds no blocking I/O, refreshes, Store authority, or extra service calls. |

**Gate result: PASS** — no violations. Proceeding to Phase 0.

## Project Structure

### Documentation (this feature)

```text
specs/018-decompose-util/
├── plan.md                    # This file
├── research.md                # Phase 0 decisions and alternatives
├── data-model.md              # Phase 1 module/data ownership
├── quickstart.md              # Phase 1 parity validation guide
└── tasks.md                   # Phase 2 output only; not created in PLAN stage
```

`contracts/` is intentionally omitted. This refactor introduces no external
HTTP, WebSocket, Home Assistant service, entity-service, event, or public API
contract. Internal request/dependency objects are specified here and in
[data-model.md](data-model.md).

### Source Code (repository root)

```text
custom_components/rental_control/
├── util.py                     # Public compatibility shell; all current names,
│                               # constants, imported deps, and monkeypatch seams
│                               # remain here
├── keymaster_services.py       # async_fire_set_code,
│                               # async_fire_clear_code,
│                               # async_fire_update_times implementations and
│                               # service-call/result builders
├── state_handlers.py           # handle_state_change implementation and
│                               # Keymaster state snapshot/normalization helpers
├── helpers.py                  # Generic stable helpers: text-state,
│                               # trim/name/identity, buffer/time, UUID,
│                               # gather/add_call, cleanup, entry data, reload
├── event_overrides.py          # Existing EventOverrides shell keeps visible
│                               # module-level wrapper names for patching
└── coordinator.py              # Existing module keeps async_fire_clear_code
                                # wrapper for patching

tests/
├── unit/
│   ├── test_util.py                  # Existing util oracle unchanged; add
│   │                                  # import/patch compatibility checks
│   ├── test_keymaster_services.py    # Focused service-helper parity cases
│   ├── test_state_handlers.py        # Focused state-change parity cases
│   ├── test_event_overrides.py       # Existing event_overrides patch oracle
│   ├── test_event_overrides_apply.py # Existing async_fire_* caller oracle
│   ├── test_coordinator.py           # Existing coordinator patch oracle
│   └── test_sensors.py               # Existing helper consumers
└── integration/
    ├── test_refresh_cycle.py         # End-to-end parity oracle
    └── test_slot_concurrency.py      # Apply/callback ordering oracle
```

**Structure Decision**: Keep `util.py` as a module, not a package. It is imported
by setup, listeners, switch, config flow, calendar, sensors, coordinator helpers,
event override helpers, coordinator, and tests. A file-to-package conversion
would add avoidable import and monkeypatch risk. Focused sibling modules provide
the decomposition while the stable `custom_components.rental_control.util` import
path remains the only public utility surface.

## Concrete Decomposition Design

### Public compatibility boundary

`custom_components/rental_control/util.py` remains importable for every current
production and test name:

- `dt`, `add_call`, `apply_buffer`, `async_fire_clear_code`,
  `async_fire_set_code`, `async_fire_update_times`,
  `async_reload_package_platforms`, `check_gather_results`,
  `compute_early_expiry_time`, `delete_folder`, `delete_rc_and_base_folder`,
  `EventIdentity`, `gen_uuid`, `get_entry_data`, `get_event_identities`,
  `get_event_names`, `get_slot_name`, `handle_state_change`,
  `is_cleared_keymaster_text_state`, `is_unreadable_keymaster_text_state`,
  `normalize_keymaster_text_state`, `normalize_uid`, `OperationResult`, and
  `trim_name`.
- Compatibility-only module attributes currently patched by tests also remain on
  `util.py`: `asyncio`, `async_track_state_change_event`, `pn_create`,
  `pn_dismiss`, and `_SET_CODE_CONFIRMATION_TIMEOUT`.

The shell should use thin wrappers for patch-sensitive names instead of simple
one-time aliases. For example, `util.async_fire_set_code()` delegates into
`keymaster_services.async_fire_set_code()` while passing the current
`util.asyncio.sleep`, `util.async_track_state_change_event`, `util.pn_create`,
`util.pn_dismiss`, and `util._SET_CODE_CONFIRMATION_TIMEOUT` dependencies. This
keeps visible tests that patch these `util` module attributes effective after the
body moves.

### Monkeypatch preservation

Hidden and visible patch targets are preserved by runtime lookup, not by copied
function aliases:

1. Hidden `custom_components.rental_control.util.async_fire_set_code`,
   `async_fire_clear_code`, `async_fire_update_times`, and
   `get_event_identities` patches intercept callers that call through `util`.
2. `event_overrides.py` keeps module-level wrappers named
   `async_fire_set_code`, `async_fire_clear_code`, `async_fire_update_times`, and
   `get_event_identities`. Each wrapper imports or references the `util` module
   and calls `util.<name>` at call time. `EventOverrides._module` continues to be
   `sys.modules[__name__]`, so visible patches to `event_overrides.<name>` still
   intercept `self._module.<name>`, and hidden patches to `util.<name>` intercept
   when the visible wrapper is not patched.
3. `coordinator.py` keeps its module-level `async_fire_clear_code` wrapper and
   `add_call` compatibility name for tests. The clear-code wrapper calls
   `util.async_fire_clear_code` at runtime. `add_call` remains a module-level
   compatibility alias or thin wrapper so patches to
   `custom_components.rental_control.coordinator.add_call` continue to intercept.
   Patching `coordinator.async_fire_clear_code` intercepts coordinator tests;
   patching `util.async_fire_clear_code` intercepts the wrapper when it is not
   patched.
4. `util.get_event_names()` remains in `util.py` as a wrapper that calls
   module-level `util.get_event_identities()` at runtime, so the event identity
   seam is patchable at `util.get_event_identities`.

This design matches the current `event_overrides` self-module pattern while
fixing the direct-alias hazard that would otherwise make a util-level patch miss
already-imported caller bindings.

### `keymaster_services.py`

`keymaster_services.py` owns the implementation of the three physical lock-code
helpers. `util.py` remains the public dispatch surface and passes a
`KeymasterServiceDeps` object containing the patch-sensitive util dependencies.

`async_fire_set_code` is split into short helpers:

- `build_slot_display_name()` applies event prefix and `trim_name` exactly as
  today.
- `build_buffered_window()` applies `apply_buffer`, coerces through
  `ensure_datetime`, and classifies `TypeError`/`ValueError` as failed results.
- `disable_slot()`, `enable_date_range()`, `write_slot_payload()`, and
  `enable_slot()` preserve current service order: disable, enable date range,
  end/start/PIN/name writes, then slot enable.
- `confirm_set_result()` waits for the expected name using the timeout and state
  tracker supplied from `util.py`, records retry success, and dismisses the same
  notification id only when a prior escalation existed.
- `operation_failure_result()` preserves cancellation propagation, retry failure
  escalation, persistent notification creation, and `OperationResult` fields.

`async_fire_clear_code` is split into short helpers:

- `verify_clear_request()` preserves lock-name and ownership guards.
- `press_reset_button()` performs the reset button service call.
- `read_clear_snapshot()` reads name and PIN states after the same propagation
  sleep supplied by `util.asyncio.sleep`.
- `classify_name_after_reset()` preserves unreadable, missing, cleared,
  lingering, and force-clear behavior.
- `classify_pin_after_reset()` preserves missing, unreadable, cleared, and
  lingering-PIN behavior.
- `finalize_clear_result()` returns confirmed, unconfirmed, or lingering results
  and records/dismisses retry state exactly as today.

`async_fire_update_times` is split into short helpers:

- `verify_update_request()` preserves slot, lock-name, and ownership guards.
- `build_update_window()` preserves buffer application and date coercion.
- `write_update_time_calls()` preserves end-before-start service-call order.
- `confirm_update_times()` waits for both datetime entities using the same
  timeout and datetime comparison logic.

All project-owned helpers must remain below 80 lines and use request/dependency
objects when more than six values would otherwise be passed.

### `state_handlers.py`

`state_handlers.py` owns the implementation behind `util.handle_state_change`.
`util.py` keeps a thin wrapper that passes `util.asyncio.sleep` so patches to
`custom_components.rental_control.util.asyncio.sleep` continue to skip the settle
delay in existing tests.

The 204-line callback is decomposed into short helpers:

- `resolve_state_change_context()` reads the coordinator, lock name, entity id,
  new state value, and current override.
- `extract_slot_number()` preserves the current regex and warning on failure.
- `handle_reset_entity()` preserves reset feedback behavior and default local-day
  update values.
- `should_ignore_suppressed_feedback()` preserves `should_suppress_state_change`.
- `read_slot_state_snapshot()` reads enabled, code, name, date-range switch, and
  start/end entities without adding service calls or refreshes.
- `normalize_slot_text_values()` preserves unreadable/cleared semantics,
  existing-override preservation during feedback, and code-without-name
  protection.
- `parse_slot_times()` preserves local-day defaults, existing override time
  preservation during feedback, and datetime parse fallback behavior.
- `restore_full_name_for_trim_match()` preserves trim/prefix full-name restoration
  only when the incoming guest name equals the expected trimmed form.
- `dispatch_override_update()` calls `coordinator.update_event_overrides()` with
  the same positional values and still does not launch reconciliation.

### `helpers.py`

`helpers.py` owns generic utility behavior that is not a Keymaster service
dispatch or state-change callback:

- text-state token, cleared, unreadable, and normalization helpers;
- `OperationResult` and `EventIdentity` data types;
- `get_entry_data`, `normalize_uid`, `check_gather_results`, `add_call`,
  recursive cleanup helpers, reload helper, and UUID generation;
- `trim_name`, `ensure_datetime`, `apply_buffer`, `compute_early_expiry_time`,
  `get_slot_name`, and event identity/name builders.

`util.py` may re-export most generic helpers directly from `helpers.py`, except
for `get_event_identities` and `get_event_names`, which remain wrappers to keep
the util-level event identity patch seam effective. If `helpers.py` approaches
400 lines during implementation, split it by stable sub-concern before claiming
complexity success rather than adding a directive.

### Behavior-equivalence strategy

Current `origin/main` source plus existing tests are the oracle. Implementation
should first add import and monkeypatch smoke tests around the existing shell,
then extract one concern at a time behind unchanged public wrappers. For
identical coordinator, event, Home Assistant state, and override inputs,
before/after results must match for:

- ordered set, clear, and update-times service calls and service data;
- operation results, retry counters, notification creation/dismissal, lingering
  flags, and cancellation propagation;
- state-change reset handling, suppression, enabled/date gates, text-state
  normalization, datetime parsing, trim/prefix restoration, and override update
  arguments;
- helper outputs for text states, buffers, slot names, event identities, UUIDs,
  cleanup, gather-result checking, entry-data lookup, reload, and early expiry;
- patch interception at `util.*`, `event_overrides.*`, and `coordinator.*`.

No helper may introduce extra Home Assistant state writes, additional
coordinator refreshes, blocking I/O, Store authority, additional Keymaster
service calls, or user-visible delays.

### Complexity and aislop

`util.py` currently has no complexity `aislop-ignore-file` directive. The
implementation must not add one. Before claiming done, measure `util.py`,
`keymaster_services.py`, `state_handlers.py`, and `helpers.py` and confirm every
utility-related file is below 400 lines, every project-owned function is below 80
lines, and every project-owned parameter list is no more than six parameters.

## Phase 0 Research

Research is complete in [research.md](research.md). It records the module/shell
choice, patch-preserving wrapper design, service-helper decomposition,
state-handler decomposition, generic helper boundary, and behavior-parity
approach, with alternatives grounded in the current source and tests.

## Phase 1 Design Artifacts

- [research.md](research.md): required and complete.
- [data-model.md](data-model.md): required and complete.
- [quickstart.md](quickstart.md): required and complete.
- `contracts/`: omitted because no external API, service, event, entity, or
  public util contract is introduced or changed.
- `update-agent-context.sh`: intentionally not run. The plan adds no new
  language, framework, database, runtime, package manager, or agent-relevant
  technology beyond the Python/Home Assistant stack already documented in the
  repository.

## Post-Design Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I: Code Quality & Testing | PASS | Quickstart requires existing util, event override, coordinator, refresh-cycle, slot-concurrency, sensor, calendar, and helper tests unchanged plus focused service/state/helper parity and patch smoke tests. |
| II: Atomic Commit Discipline | PASS | PLAN artifacts are one docs-only change; future runtime work can be split into small extraction, wrapper, and test commits. |
| III: Licensing & Attribution | PASS | `plan.md`, `research.md`, `data-model.md`, and `quickstart.md` include SPDX headers. |
| IV: Pre-Commit Integrity | PASS | The PR must pass hooks and CI without bypass flags. |
| V: Agent Co-Authorship & DCO | PASS | The planned commit uses sign-off and the requested co-author trailer. |
| VI: User Experience Consistency | PASS | Full util import surface and util/event_overrides/coordinator patch seams are preserved with behavior-equivalent service and callback outcomes. |
| VII: Performance Requirements | PASS | Extracted helpers preserve existing waits and service order and add no I/O, refreshes, state writes, or extra Keymaster operations. |

**Gate result: PASS** — no plan-stage constitution violations. Existing
`util.py` complexity debt remains the implementation target.

## Complexity Tracking

> No plan-stage constitution violations require justification. The existing util
> file-size and function-length findings remain the implementation target, and
> the implementation must measure file lengths, function lengths, and parameter
> counts before considering the decomposition complete.

## Phase Notes

- PLAN stage stops here. Do not create `tasks.md` or modify runtime source in
  this PR.
- Implementation must treat current `origin/main` as truth. Planning shorthand
  and issue text are secondary when they disagree with `util.py` or visible patch
  sites.
- Keep the refactor behavior-preserving. Any discovered lock-code, state-change,
  self-heal, or helper behavior improvement belongs in a separate issue/feature.
