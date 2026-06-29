<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Research: Decompose Util

## Decision: Keep `util.py` as a module compatibility shell

**Rationale**: `util.py` is imported by setup, listeners, switch, config flow,
calendar, sensors, coordinator helpers, event override helpers, coordinator, and
many tests. It also hosts direct monkeypatch targets. Keeping the file as the
stable import path avoids package-conversion risk and allows implementation to
move behavior behind wrappers without caller rewrites.

**Alternatives considered**:

- Convert `util.py` to `util/__init__.py`: rejected because package conversion
  adds avoidable import and monkeypatch risk for no behavior benefit.
- Move callers directly to new modules: rejected because the specification
  requires existing imports and tests to keep working unchanged.

## Decision: Use focused sibling modules

**Rationale**: The current file mixes physical Keymaster service writes,
Keymaster callback handling, and generic pure helpers. Sibling modules preserve
simple relative imports while separating concerns:

- `keymaster_services.py` owns `async_fire_set_code`, `async_fire_clear_code`,
  and `async_fire_update_times` implementations.
- `state_handlers.py` owns `handle_state_change` implementation.
- `helpers.py` owns stable generic behavior such as text-state normalization,
  `trim_name`, `apply_buffer`, event identities, UUIDs, gather checking,
  cleanup, entry data, and reload.

**Alternatives considered**:

- One new `util_helpers/` package: rejected for this stage because three sibling
  modules are enough to resolve the current findings and are easier to keep
  aligned with the issue's concern boundaries.
- A single replacement helper module: rejected because it would recreate the
  ambiguous catch-all shape that the feature is meant to remove.

## Decision: Preserve patch seams through runtime wrappers

**Rationale**: Visible tests patch `event_overrides.async_fire_*`,
`coordinator.async_fire_clear_code`, `util.asyncio.sleep`, `util.pn_create`,
`util.pn_dismiss`, `util.async_track_state_change_event`, and
`util._SET_CODE_CONFIRMATION_TIMEOUT`. The specification also requires hidden
patches to `util.async_fire_*` and `util.get_event_identities` to remain
effective. Direct import aliases would freeze old function objects and make
util-level patches miss event-overrides or coordinator call sites.

Implementation therefore keeps wrapper functions at each public patch boundary.
`event_overrides` and `coordinator` wrappers call the `util` module at runtime;
`util` wrappers pass the current util-level dependency attributes into extracted
implementations.

**Alternatives considered**:

- Re-export extracted functions with `from .keymaster_services import ...`:
  rejected because caller modules that import those names would not observe later
  patches to `util.<name>`.
- Change tests to patch new modules: rejected because this must be a
  behavior-preserving decomposition with unchanged visible tests.

## Decision: Extract service helpers by operation phase

**Rationale**: The Keymaster helpers are safety-critical and currently exceed the
function-length threshold. Splitting by existing operation phases preserves order
and makes parity tests direct: guard, build, dispatch, confirm, finalize. Set,
clear, and update-times each keep the same public signature while private helpers
stay below 80 lines.

**Alternatives considered**:

- Share one generic service dispatcher: rejected because set, clear, and
  update-times have different safety gates and result classifications. A generic
  dispatcher would obscure the ordered physical side effects.
- Keep the long functions and add an `aislop` directive: rejected because the
  findings are live and the specification forbids suppression.

## Decision: Pass dependency objects to extracted implementations

**Rationale**: Several current tests patch dependencies on `util.py`. A
`KeymasterServiceDeps` and `StateHandlerDeps` object keeps helper signatures
short and ensures extracted logic uses the patched `sleep`, notification,
confirmation timeout, and state-change tracking attributes supplied by the shell.

**Alternatives considered**:

- Import `asyncio`, notification helpers, and HA event tracking directly in new
  modules: rejected because existing `util.*` patches would stop intercepting.
- Add many positional dependency parameters: rejected because it risks exceeding
  the six-parameter threshold and makes call sites harder to audit.

## Decision: Decompose `handle_state_change` by read/normalize/update stages

**Rationale**: The callback's behavior is a staged pipeline: resolve context,
extract slot, handle reset/suppression, read entity snapshot, normalize text
values, parse times, restore full names, and update overrides. Encoding that
pipeline as helpers gives focused tests for each conservative early return while
preserving the no-reconciliation rule.

**Alternatives considered**:

- Move the callback wholesale to `state_handlers.py`: rejected because the over
  80-line finding would remain.
- Change coordinator override update semantics: rejected as a behavior change and
  explicit non-goal.

## Decision: Keep generic helper semantics centralized

**Rationale**: Text-state normalization and `apply_buffer` are load-bearing for
self-heal. Event identity and slot-name helpers are shared by event overrides,
coordinator helpers, sensors, and tests. Keeping these exact semantics in one
focused module prevents drift while allowing `util.py` to remain a thin public
surface.

**Alternatives considered**:

- Duplicate small helpers in each consumer: rejected because divergent self-heal
  or matching semantics would be hard to detect.
- Move helper imports directly to caller modules: rejected because current util
  imports are compatibility requirements.

## Decision: Omit contracts and agent-context updates

**Rationale**: This is an internal refactor plan. It adds no external API,
service schema, entity contract, event payload, runtime dependency, or new
technology. Existing Home Assistant service calls and public Python import
surfaces are preserved rather than extended.

**Alternatives considered**:

- Add contract files for internal helpers: rejected because data-model.md and
  plan.md already define internal request/dependency objects, and no external
  contract is introduced.
- Run `update-agent-context.sh`: rejected because no new language, framework, or
  dependency is introduced.
