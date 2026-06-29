<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Feature Specification: Decompose Init Startup Readability

**Feature Branch**: `021-decompose-init-startup`
**Created**: 2026-06-29
**Status**: Draft
**Input**: User description: "Decompose
`custom_components/rental_control/__init__.py` for GitHub issue #658. This
is a behavior-preserving code-health refactor of the remaining startup
Keymaster readability refresh concern. Extract the startup-readability watcher
and helpers from the Home Assistant entry module, decompose the long arming
function, and keep all current public entry-point and monkeypatch surfaces
compatible."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Preserve Startup Readability Refresh (Priority: P1)

As a property manager using Keymaster-backed Rental Control locks, I want the
startup readability refresh to behave exactly as it does today, so the
integration still performs one corrective coordinator refresh when startup lock
slot entities become readable after initially being unavailable.

**Why this priority**: The startup readability watcher protects lock-code
state during Home Assistant startup when Keymaster slot entities may not be
readable during the first coordinator refresh. Behavior drift could miss a
needed refresh, refresh more than once, or leave stale access state.

**Independent Test**: Can be fully tested by running the existing startup
readability tests unchanged and by comparing watcher arming, delayed refresh,
missed-transition handling, unload cleanup, and no-watch behavior for readable
startup slots before and after decomposition.

**Acceptance Scenarios**:

1. **Given** all managed Keymaster slot entities are readable at startup,
   **When** Rental Control setup completes, **Then** no startup readability
   watcher is armed beyond the normal listeners.
2. **Given** managed Keymaster slot entities are unavailable during the first
   startup refresh, **When** they transition to readable states, **Then** one
   delayed coordinator refresh is scheduled after the same debounce interval
   and no additional one-shot refreshes are scheduled.
3. **Given** the startup slot entities become readable before the watcher is
   armed but setup recorded unreadable startup slots, **When** the watcher arms,
   **Then** it still schedules the same one-shot refresh after the debounce
   interval.
4. **Given** the config entry unloads before the watched entities settle,
   **When** later state changes or timers fire, **Then** the watcher, debounce
   timer, watchdog, and pending refresh task are cancelled and no refresh runs.
5. **Given** watched entities never settle before the watchdog expires,
   **When** the watchdog fires, **Then** the watcher removes itself and leaves
   listener cleanup in the same state as today.

---

### User Story 2 - Preserve Integration Entry Contract (Priority: P1)

As a Home Assistant user and Rental Control maintainer, I want the integration
entry module to keep the same setup, unload, and update-listener behavior after
decomposition, so Home Assistant lifecycle handling and existing tests continue
to use the same package-level contract.

**Why this priority**: `__init__.py` is the Home Assistant entry point for the
integration. It currently owns setup orchestration, unload cleanup,
configuration update handling, listener startup, and package-level re-exports
from the prior #572 decomposition. Those contracts must remain stable.

**Independent Test**: Can be fully tested by running existing `test_init.py`
and integration setup tests unchanged, including setup success and retry,
platform forwarding, unload cleanup, config-entry reload, update-listener
behavior, and current package-level imports.

**Acceptance Scenarios**:

1. **Given** Home Assistant sets up a Rental Control config entry, **When** the
   decomposed integration runs, **Then** coordinator creation, slot-store
   loading, Keymaster override bootstrap, first refresh, startup readability
   arming, listener startup, platform forwarding, keymaster listener
   registration, update-listener registration, and generated-file cleanup occur
   in the same order and with the same outcomes.
2. **Given** Home Assistant unloads a Rental Control config entry, **When** the
   decomposed integration runs unload cleanup, **Then** platform unload,
   generated-file cleanup, package reload, unsubscribe cleanup, domain-data
   removal, notification dismissal, and return value remain unchanged.
3. **Given** config-entry options are applied through `update_listener`,
   **When** existing entry data is present or disappears at any guarded point,
   **Then** the same data mutation, coordinator update, listener cleanup,
   listener restart, keymaster listener registration, or safe early return occurs.
4. **Given** Home Assistant or tests import package-level entry names, **When**
   the decomposed package is loaded, **Then** all existing public entry points
   remain importable and callable from `custom_components.rental_control`.

---

### User Story 3 - Preserve Current Test and Patch Surfaces (Priority: P1)

As a Rental Control maintainer, I want visible and hidden tests to keep their
current imports and monkeypatch seams, so the refactor can be reviewed as a
small behavior-preserving split rather than a coordinated test rewrite.

**Why this priority**: Visible tests import
`async_arm_startup_readability_refresh` directly from
`custom_components.rental_control`, call it directly for missed-transition
coverage, and patch `custom_components.rental_control.async_start_listener`
while exercising `update_listener`. Hidden tests may rely on the same
package-level compatibility surface.

**Independent Test**: Can be fully tested by running existing tests unchanged
and by verifying direct package imports, direct calls, and module-path patches
continue to affect the same runtime behavior.

**Acceptance Scenarios**:

1. **Given** `tests/unit/test_init.py` imports
   `async_arm_startup_readability_refresh` from `custom_components.rental_control`,
   **When** the startup-readability concern is extracted, **Then** that import
   still resolves and the callable preserves the same signature and behavior.
2. **Given** tests call `async_arm_startup_readability_refresh` directly with
   `startup_slots_unreadable=True`, **When** slot entities are already readable,
   **Then** the same delayed one-shot refresh and cleanup behavior occurs.
3. **Given** tests patch `custom_components.rental_control.async_start_listener`,
   **When** `update_listener` restarts listeners for a lock-backed entry,
   **Then** the patch remains effective at the package module path.
4. **Given** package-level names imported from the prior #572 split remain
   available, **When** hidden tests or callers import migration and keymaster
   listener entry points, **Then** the decomposition does not remove or rename
   those compatibility names.

---

### User Story 4 - Resolve Remaining Aislop Complexity Findings (Priority: P2)

As a maintainer, I want the last startup-readability concern split out of the
entry module and decomposed into focused units, so the integration is fully
under the active Aislop file-size and function-length thresholds without
suppressing findings.

**Why this priority**: Issue #658 reports the last two live complexity
findings in the integration: `__init__.py` is 449 lines against a 400-line
threshold, and `async_arm_startup_readability_refresh` is 143 lines against an
80-line threshold. The previous #572 split extracted `migrations.py` and
`listeners.py`, but startup-readability logic kept the entry module over the
limit.

**Independent Test**: Can be fully tested by measuring the decomposed
integration entry area against active complexity thresholds while existing
behavior tests continue to pass unchanged.

**Acceptance Scenarios**:

1. **Given** the decomposition is complete, **When** `__init__.py` is measured,
   **Then** it is below the 400-line threshold and remains focused on Home
   Assistant entry-point orchestration.
2. **Given** the startup-readability arming behavior is inspected after
   decomposition, **When** project-owned functions are measured, **Then** the
   public arming function and extracted helpers are each below the 80-line
   function-length threshold.
3. **Given** `async_arm_startup_readability_refresh` has four parameters today,
   **When** the function is decomposed, **Then** parameter-count compliance is
   preserved and no new project-owned function exceeds the six-parameter limit.
4. **Given** no startup-readability complexity directive exists today, **When**
   the findings are resolved, **Then** no new Aislop ignore or suppression
   directive is added for file size, function length, or parameter count.
5. **Given** planning and implementation decide exact helper boundaries, **When**
   work is scoped, **Then** the split is by coherent startup-readability concern
   and does not depend on exact module, class, or helper names prescribed by this
   specification.

---

### Edge Cases

- What happens when no lock name is configured? Startup readability entity
  discovery continues to return no watched entities and no watcher is armed.
- What happens when there are no managed slot entities for the configured
  range? The arming decision remains a no-op with the same returned readability
  state as today.
- What happens when a watched entity state is `None` or `unavailable`? It
  remains unreadable; only present states other than `unavailable` are treated
  as readable, including `unknown` and normal switch/text states.
- What happens when a state change reports a readable new state but the old
  state was already readable? The debounce timer is not rescheduled, preserving
  the current storm-filtering behavior.
- What happens when multiple watched entities transition rapidly from
  unreadable to readable? Debounce cancellation and rescheduling still collapse
  the storm into a single readiness check and one refresh.
- What happens when entry data disappears before the refresh coroutine runs?
  The refresh returns safely without calling the coordinator.
- What happens when the refresh raises an exception? The exception is logged
  with the same non-fatal behavior and cleanup continues.
- What happens when unload cleanup runs while a refresh task is pending? The
  pending task is cancelled and the watcher removes its listener reference.
- What happens when current or hidden tests import or patch package-level
  `__init__` names? The current compatibility paths remain available and
  effective.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The decomposition MUST preserve all Home Assistant observable
  behavior of the integration entry module, including setup, unload,
  update-listener, listener startup, package-level re-exports, startup
  readability arming, and cleanup behavior.
- **FR-002**: Existing `tests/unit/test_init.py` behavior tests MUST pass
  unchanged after the implementation stage; any new tests MUST verify behavior
  parity or focused extracted behavior rather than introduce new runtime
  behavior.
- **FR-003**: `async_arm_startup_readability_refresh` MUST remain importable and
  callable from `custom_components.rental_control` with the current parameters:
  Home Assistant instance, config entry, coordinator, and keyword-only
  `startup_slots_unreadable` defaulting to false.
- **FR-004**: `async_setup_entry` MUST continue calling the startup readability
  arming behavior after the first coordinator refresh and before normal listener
  startup, using the startup unreadability result captured before the first
  refresh.
- **FR-005**: The startup readability decision MUST remain equivalent for lock
  names, managed slot ranges, watched entity IDs, missing states, unavailable
  states, readable unknown states, and already-readable startup states.
- **FR-006**: The watcher lifecycle MUST remain equivalent: state-change
  tracking, debounce timer, watchdog timer, unload listener reference, pending
  refresh task, cancellation order, self-removal, and listener-list cleanup must
  produce the same observable outcomes.
- **FR-007**: The one-shot refresh guarantee MUST remain equivalent: once all
  watched managed-slot entities are readable, exactly one coordinator refresh is
  scheduled after the current delay, and subsequent state changes or timers do
  not schedule additional startup readability refreshes.
- **FR-008**: The missed-transition behavior MUST remain equivalent: if startup
  observed unreadable slots but all watched entities are readable by the time the
  watcher arms, the same delayed one-shot refresh is scheduled.
- **FR-009**: Watchdog expiry MUST remain equivalent: if the watched entities do
  not become readable before the current watchdog interval, the watcher logs the
  same non-fatal expiration outcome and removes itself from cleanup tracking.
- **FR-010**: Refresh error handling MUST remain equivalent: missing entry data
  skips the refresh safely, coordinator refresh exceptions are logged and not
  propagated, and cleanup removes the watcher reference when the task completes.
- **FR-011**: The package-level `async_start_listener` monkeypatch seam MUST
  remain patchable at `custom_components.rental_control.async_start_listener`
  and must continue to affect `update_listener` behavior in existing tests.
- **FR-012**: The package-level Home Assistant contract MUST remain unchanged
  for `async_setup_entry`, `async_unload_entry`, `update_listener`,
  `async_start_listener`, `async_migrate_entry`,
  `async_register_keymaster_listener`, and
  `async_arm_startup_readability_refresh`.
- **FR-013**: The prior #572 decomposition boundaries for migrations and
  keymaster listeners MUST remain intact unless a caller-preserving import or
  re-export adjustment is required by the startup-readability split.
- **FR-014**: The completed decomposition MUST keep `__init__.py` below 400
  lines, project-owned functions below 80 lines, and project-owned parameter
  lists at no more than 6 parameters unless an external Home Assistant
  signature requires otherwise.
- **FR-015**: The implementation MUST NOT add any new Aislop ignore or
  suppression directive, including `aislop-ignore`, `aislop-ignore-file`, or
  equivalent directives, to hide file-size, function-length, or parameter-count
  findings.
- **FR-016**: Planning and implementation documentation MUST state that this is a
  behavior-preserving refactor and MUST NOT define new lock-code business rules,
  startup refresh semantics, listener semantics, configuration options, services,
  entities, diagnostics fields, or changed public caller behavior.

### Key Entities

- **Integration Entry Shell**: The Home Assistant-facing package module that
  owns setup, unload, update-listener orchestration, listener startup calls, and
  package-level compatibility names while delegating detailed concerns.
- **Startup Readability Concern**: The currently self-contained logic that
  identifies managed Keymaster slot entities, determines whether they are
  readable, arms state and timer watchers, and performs a one-shot coordinator
  refresh once startup readability is restored.
- **Managed Slot Entity Set**: The text and switch entities for each configured
  managed Keymaster slot whose startup readability determines whether the
  corrective refresh should be armed.
- **Startup Readability Watcher**: The lifecycle unit that owns the state-change
  subscription, debounce timer, watchdog timer, unload cleanup callback, pending
  refresh task, and self-removal behavior.
- **Package-Level Compatibility Surface**: The names importable from
  `custom_components.rental_control` that Home Assistant, visible tests, hidden
  tests, or project modules may consume, including setup, unload, migration,
  update listener, listener startup, keymaster listener registration, and
  startup readability arming.
- **Complexity Threshold**: The active Aislop limits for this issue: files below
  400 lines, project-owned functions below 80 lines, and project-owned parameter
  lists with no more than 6 parameters.

## Assumptions

- This specification covers issue #658's spec stage only; planning and
  implementation stages will decide exact helper boundaries, module layout,
  object shape, callback names, and compatibility mechanics.
- The live source read for this specification is a 449-line
  `custom_components/rental_control/__init__.py`, above the active 400-line
  file threshold.
- The active function-length finding is
  `async_arm_startup_readability_refresh` at 143 lines. The function currently
  has four parameters, so parameter count is not the reported violation but must
  remain within the active limit.
- The startup-readability concern also includes the module-level helpers
  `_managed_slot_readability_entity_ids`, `_is_readable_keymaster_state`,
  `_all_managed_slots_readable`, and `_needs_startup_readability_refresh`.
- The long arming function currently contains nested cleanup, cancellation,
  refresh, schedule, and watchdog closures. Planning may promote those
  responsibilities to focused helpers or a small watcher object as long as
  behavior and public compatibility are unchanged.
- The previous #572 implementation already extracted migration and keymaster
  listener responsibilities to `migrations.py` and `listeners.py`; this feature
  must not reopen that scope beyond preserving package-level compatibility.
- Current visible consumers are `async_setup_entry` calling
  `async_arm_startup_readability_refresh`, `tests/unit/test_init.py` importing
  and directly calling `async_arm_startup_readability_refresh`, and
  `tests/unit/test_init.py` patching
  `custom_components.rental_control.async_start_listener` while testing
  `update_listener`.
- Runtime performance expectations are parity with the current implementation in
  normal Home Assistant operation, not a new user-visible performance feature.

## Non-Goals

- Changing Home Assistant-visible setup, unload, update-listener, platform
  forwarding, migration, keymaster listener, generated-file cleanup, or package
  listener behavior.
- Changing startup readability detection, watched entity IDs, readable-state
  rules, debounce delay, watchdog interval, one-shot refresh timing,
  cancellation semantics, logging behavior, or refresh error handling.
- Adding new configuration options, services, entities, diagnostics fields,
  automations, Store authority, recovery workflows, or lock-code business rules.
- Changing the package-level import, direct-call, and monkeypatch surfaces
  consumed by current production callers, visible tests, or hidden tests.
- Prescribing exact file names, helper module names, class names, object field
  names, or helper signatures for the plan and implementation stages.
- Adding any Aislop ignore or suppression directive for `__init__.py` or
  startup-readability complexity findings.
- Closing issue #658 in this specification PR; later implementation work owns
  the runtime refactor.

## Constraints

- No production code changes are allowed in this specification PR.
- No behavior observable by Home Assistant users, dashboards, automations,
  services, physical Keymaster state, stored config-entry data, coordinator
  state, listener cleanup, logs relied on by tests, or existing tests may change
  as part of this refactor.
- `__init__.py` MUST remain the Home Assistant entry-point shell for
  `async_setup_entry`, `async_unload_entry`, and `update_listener`.
- `async_arm_startup_readability_refresh` MUST remain importable from
  `custom_components.rental_control`, even if its implementation moves to a
  dedicated startup-readability module.
- `async_start_listener` MUST remain patchable at
  `custom_components.rental_control.async_start_listener` for current tests.
- The final implementation MUST satisfy the active file-size, function-length,
  and parameter-count thresholds without adding suppressing directives.
- This work MUST remain behavior-preserving and limited to the startup
  readability concern plus required package-level import or re-export plumbing.

## Security Considerations

- Startup readability affects when lock-code reconciliation refreshes after Home
  Assistant startup. Missing or duplicating the one-shot refresh can leave
  managed Keymaster slot state stale or unexpectedly churn access-code state.
- Readability detection must remain conservative for missing and unavailable
  entities so startup does not treat unreadable physical lock state as ready.
- Listener, timer, watchdog, and task cleanup must remain exact to avoid stale
  callbacks or refresh tasks running after unload against removed lock
  configuration.
- Logs and helper boundaries must continue to expose no more calendar, guest,
  lock, slot, or code information than existing Rental Control behavior already
  exposes.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of existing `tests/unit/test_init.py` tests pass unchanged
  after the implementation stage completes, including startup readability,
  setup, unload, reload, and update-listener coverage.
- **SC-002**: In 100% of covered startup readability scenarios, watcher arming,
  entity readability detection, debounce scheduling, missed-transition refresh,
  watchdog expiry, unload cancellation, refresh task cleanup, and refresh error
  handling match the current implementation.
- **SC-003**: Home Assistant can still initialize, unload, reload, and update the
  integration through the same package-level setup, unload, update-listener,
  migration, listener-start, and keymaster-listener contracts without caller
  changes.
- **SC-004**: All visible tests that import
  `async_arm_startup_readability_refresh`, call it directly, import
  `update_listener`, or patch
  `custom_components.rental_control.async_start_listener` continue to do so
  without behavior changes or behavior-assertion rewrites.
- **SC-005**: The decomposed integration entry area contains no files of 400
  lines or more, no project-owned functions of 80 lines or more, and no
  project-owned parameter lists over 6 parameters.
- **SC-006**: Active complexity checks pass without adding any Aislop ignore or
  suppression directive for file size, function length, or parameter count.
- **SC-007**: Normal startup readability processing performs no additional
  coordinator refreshes, Home Assistant state writes, config-entry mutations,
  Keymaster service calls, blocking I/O, async tasks, or user-visible delays
  compared with the current implementation, except for the existing one-shot
  refresh behavior when startup slots become readable.
- **SC-008**: No production-code changes are included in this specification PR;
  the PR contains only the feature specification for the #658 decomposition
  pipeline.
