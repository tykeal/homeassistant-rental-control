<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Feature Specification: Decompose Util

**Feature Branch**: `018-decompose-util`
**Created**: 2026-06-28
**Status**: Draft
**Input**: User description: "Decompose
`custom_components/rental_control/util.py` for GitHub issue #578. This is a
behavior-preserving code-health refactor of the oversized Rental Control utility
module. Split Keymaster service helpers, state-change handling, and generic
helpers into focused, independently testable units without changing runtime
behavior or the compatibility surface imported from `util.py`."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Preserve Keymaster Service Safety (Priority: P1)

As a property manager, I want set, clear, and update-times operations to issue
the same Keymaster service calls in the same order after decomposition, so
upgrades cannot program the wrong slot, clear an occupied slot unsafely, or
change guest access windows.

**Why this priority**: `async_fire_set_code`, `async_fire_clear_code`, and
`async_fire_update_times` directly mutate physical lock-code slots. They contain
ownership checks, buffering, retry escalation, confirmation waits, and lingering
clear detection that protect physical access.

**Independent Test**: Can be fully tested by running existing utility,
event-override, coordinator, refresh-cycle, slot-concurrency, and sensor tests
unchanged and by comparing ordered Home Assistant service calls, operation
results, retry notifications, and observed slot states for identical inputs
before and after decomposition.

**Acceptance Scenarios**:

1. **Given** a set-code operation for a verified slot, **When** it runs after
   decomposition, **Then** it performs the same prefix and trim calculation,
   buffer application, disable, date-range enable, end/start/PIN/name writes,
   slot-enable call, confirmation wait, retry-success handling, and result
   classification as today.
2. **Given** a clear-code operation for a verified slot, **When** Keymaster reset
   succeeds, fails, leaves unreadable state, leaves a lingering name, or leaves a
   lingering PIN, **Then** the same reset call, forced name clear, propagation
   wait, retry escalation, notification dismissal, and `OperationResult` flags
   are produced.
3. **Given** an update-times operation for a verified slot, **When** buffered
   start and end times are applied, **Then** the same end/start service calls,
   error handling, confirmation waits, and confirmed or unconfirmed result are
   produced.
4. **Given** ownership verification fails or no lock name is configured, **When**
   any Keymaster service helper is invoked, **Then** the same conservative
   unconfirmed result is returned and no unsafe service write is performed.
5. **Given** tests patch the Keymaster helper call sites, **When** the
   decomposed helpers are loaded, **Then** patching the current `util.*`,
   `event_overrides.*`, and `coordinator.*` monkeypatch targets continues to
   intercept the same behavior without test rewrites.

---

### User Story 2 - Preserve State-Change Handling (Priority: P1)

As an existing Rental Control user, I want Keymaster state-change callbacks to
update override state exactly as before, so manual Keymaster edits, feedback from
Rental Control service calls, unreadable states, reset buttons, and trimmed names
continue to be interpreted safely.

**Why this priority**: `handle_state_change` is the bridge from Home Assistant
state changes into coordinator override state. Behavior drift can make occupied
slots appear free, preserve stale codes, overwrite manual edits, or restart
reconciliation from a callback path that must remain passive.

**Independent Test**: Can be fully tested by running existing
`handle_state_change` unit and integration tests unchanged and by replaying
reset events, suppressed feedback events, enabled-state changes, code/name/date
changes, unreadable states, empty states, and trim/prefix scenarios with
identical override updates.

**Acceptance Scenarios**:

1. **Given** a Keymaster reset entity changes, **When** the callback handles the
   event, **Then** it clears the same override slot to empty code, empty name,
   and local-day start/end values as today.
2. **Given** Rental Control generated feedback should be suppressed, **When** the
   state-change event arrives, **Then** the same suppression check short-circuits
   processing without updating overrides.
3. **Given** a slot is disabled, missing, unreadable, code-bearing but unnamed,
   or has an unparsable datetime, **When** the callback processes it, **Then** the
   same conservative early-return or default-time behavior is preserved.
4. **Given** Keymaster reports a trimmed and optionally prefixed display name,
   **When** the stored override contains the untrimmed name, **Then** the same
   full-name restoration occurs only when the current trimmed-name comparison
   would restore it.
5. **Given** a normal state change for code, name, or date range, **When**
   processing completes, **Then** `update_event_overrides` receives the same slot,
   code, name, start, and end values and the callback does not launch
   reconciliation.

---

### User Story 3 - Preserve Public Util Compatibility Surface (Priority: P1)

As a Rental Control maintainer, I want every symbol currently imported from
`util.py` by production modules and tests to remain importable from `util.py`, so
this refactor can be reviewed as a behavior-preserving decomposition rather than
a coordinated caller rewrite.

**Why this priority**: `util.py` is heavily consumed by setup, coordinator,
coordinator helper, sensor, calendar, config-flow, switch, listener, and
event-override code. It is also a direct test import surface and a monkeypatch
boundary for Keymaster helpers and event identity helpers.

**Independent Test**: Can be fully tested by running existing tests unchanged and
by verifying all current production and test import sites continue to load the
same names from `custom_components.rental_control.util` with the same call
patterns and observable results.

**Acceptance Scenarios**:

1. **Given** production code imports helpers from `.util`, `..util`, or
   `...util`, **When** the decomposed implementation is loaded, **Then** every
   currently consumed util symbol remains importable from `util.py` without
   caller-side import changes.
2. **Given** tests import `OperationResult`, `EventIdentity`, helper functions,
   service helpers, and `handle_state_change` directly from `util.py`, **When**
   the test suite runs, **Then** those imports continue to work unchanged.
3. **Given** hidden or future regression tests patch
   `custom_components.rental_control.util.async_fire_set_code`,
   `custom_components.rental_control.util.async_fire_clear_code`,
   `custom_components.rental_control.util.async_fire_update_times`, or
   `custom_components.rental_control.util.get_event_identities`, **When** the
   patch is applied, **Then** the same runtime seam remains patchable at the
   `util.<name>` path exactly as today.
4. **Given** visible tests patch `event_overrides.async_fire_*` and
   `coordinator.async_fire_clear_code`, **When** those modules use the decomposed
   implementation, **Then** the current patch paths remain effective.
5. **Given** coordinator helpers import `dt` through `util.py`, **When** those
   helpers run, **Then** that compatibility import remains available unless a
   later behavior-changing issue explicitly migrates callers.

---

### User Story 4 - Preserve Generic Helper Semantics (Priority: P1)

As a maintainer, I want the generic helper functions to keep their exact
semantics after they move into focused units, so self-healing, slot naming,
calendar identity, buffer handling, cleanup, and reload behavior do not change.

**Why this priority**: Several short helpers are load-bearing despite being
small. In particular, empty/unreadable Keymaster-state helpers and `apply_buffer`
are required by stateless self-heal and must remain exact.

**Independent Test**: Can be fully tested by running existing utility,
coordinator-helper, event-override-helper, reservation, matcher, and sensor tests
unchanged and by comparing helper outputs for the same inputs before and after
decomposition.

**Acceptance Scenarios**:

1. **Given** Keymaster text states are `None`, empty, `unknown`, `none`,
   `unavailable`, whitespace padded, mixed case, or populated, **When** the
   empty/unreadable/normalize helpers evaluate them, **Then** the same cleared,
   unreadable, normalized-empty, normalized-`None`, or populated value is
   returned.
2. **Given** buffers are zero, positive, or applied to `date` values, **When**
   `apply_buffer` runs, **Then** the same unchanged values or timezone-aware
   datetimes are returned with the same before/after minute adjustments.
3. **Given** event summaries, descriptions, prefixes, UIDs, date values, and
   datetimes are processed, **When** slot-name and event-identity helpers run,
   **Then** the same names, exclusions, normalized UIDs, and event identities are
   produced.
4. **Given** helper consumers perform gather-result checking, cleanup, UUID
   generation, early-expiry calculation, entry-data lookup, package reload, and
   service-call collection, **When** those helpers run, **Then** their existing
   return values, exceptions, logging behavior, and side effects are preserved.

---

### User Story 5 - Improve Maintainability Under Aislop Limits (Priority: P2)

As a maintainer, I want the 1,173-line grab-bag utility module split by concern
into focused, independently testable units, so future fixes can target Keymaster
service calls, state-change handling, and generic helpers without navigating one
oversized module with live complexity findings.

**Why this priority**: Issue #578 identifies `util.py` as over the 400-line file
threshold with four functions over the 80-line limit. Unlike recent coordinator
and event-override work, `util.py` has no `aislop-ignore-file` directive, so the
findings are active and should be resolved without adding suppressions.

**Independent Test**: Can be fully tested by measuring the decomposed utility
feature area against active complexity thresholds and by adding focused tests for
extracted concerns while existing behavior tests continue to pass unchanged.

**Acceptance Scenarios**:

1. **Given** the decomposition is complete, **When** complexity checks run,
   **Then** utility-related files are below 400 lines, project-owned functions
   are below 80 lines, and project-owned parameter lists have no more than 6
   parameters.
2. **Given** the refactor is complete, **When** `util.py` is inspected, **Then**
   it remains a compatibility import surface and no longer owns the detailed
   Keymaster service-helper, state-change, and generic-helper implementations.
3. **Given** no util complexity directive exists today, **When** implementation
   resolves the findings, **Then** no new `aislop-ignore-file` directive is added
   to hide file-size or function-length complexity.
4. **Given** planning and implementation decide exact helper boundaries, **When**
   work is scoped, **Then** the split is by coherent concern and does not create a
   new ambiguous catch-all utility module.

---

### Edge Cases

- What happens when Keymaster text states are empty, `unknown`, `none`, `None`,
  whitespace-padded, mixed-case, or `unavailable`? The existing cleared and
  unreadable helper semantics remain exact and continue to drive self-heal
  decisions conservatively.
- What happens when a slot clear succeeds but Keymaster keeps a lingering name or
  PIN? The same forced name-clear attempt, lingering flags, unconfirmed result,
  and retry bookkeeping are preserved.
- What happens when set or update-time buffering receives bare `date` values,
  timezone-aware datetimes, invalid values, or non-integer optional buffers? The
  same datetime normalization, fallback, failure, and result behavior is
  retained.
- What happens when a Keymaster helper service call raises, is cancelled, or
  leaves state unconfirmed? Cancellation still propagates; ordinary failures,
  retry escalation, persistent notifications, confirmation waits, and operation
  results match the current implementation.
- What happens when a state-change callback observes feedback from a Rental
  Control write? The same suppression markers prevent loops and preserve the
  prior override values when the current code would do so.
- What happens when the callback sees a code without a readable name? The slot
  remains out of the free pool exactly as today and no unsafe override update is
  written.
- What happens when a trimmed Keymaster name collides with a manual edit or
  prefix? Existing trim, prefix, and full-name restoration rules remain the sole
  source of truth.
- What happens when callers patch util or imported helper paths? The current
  patch boundaries remain present and effective so existing and hidden tests can
  intercept behavior without import rewrites.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The decomposition MUST preserve all Home Assistant observable
  behavior of `custom_components/rental_control/util.py`, including service-call
  order, operation results, state-change override updates, helper outputs,
  exceptions, logging decisions relied on by tests, notifications, and caller
  import behavior.
- **FR-002**: Existing util-related unit and integration tests MUST pass
  unchanged after the implementation stage; new tests MUST verify behavior parity
  and focused extracted behavior rather than introduce new runtime behavior.
- **FR-003**: Every current top-level public name importable through `util.py`
  MUST remain importable from `custom_components.rental_control.util` after
  decomposition, with the same call patterns and behavior. At minimum, the
  currently consumed compatibility surface is `dt`,
  `is_cleared_keymaster_text_state`,
  `is_unreadable_keymaster_text_state`, `normalize_keymaster_text_state`,
  `OperationResult`, `get_entry_data`, `normalize_uid`,
  `check_gather_results`, `add_call`, `delete_rc_and_base_folder`,
  `delete_folder`, `async_fire_clear_code`, `trim_name`, `apply_buffer`,
  `async_fire_set_code`, `async_fire_update_times`, `EventIdentity`,
  `get_event_identities`, `get_event_names`, `gen_uuid`,
  `compute_early_expiry_time`, `get_slot_name`, `handle_state_change`, and
  `async_reload_package_platforms`.
- **FR-004**: The Keymaster helper monkeypatch seams MUST remain patchable at
  `custom_components.rental_control.util.async_fire_set_code`,
  `custom_components.rental_control.util.async_fire_clear_code`, and
  `custom_components.rental_control.util.async_fire_update_times` exactly as
  today, even if their implementation moves behind the compatibility boundary.
- **FR-005**: The event identity monkeypatch seam MUST remain patchable at
  `custom_components.rental_control.util.get_event_identities` exactly as today.
- **FR-006**: Existing visible patch targets imported into caller modules MUST
  remain effective, including
  `custom_components.rental_control.event_overrides.async_fire_set_code`,
  `custom_components.rental_control.event_overrides.async_fire_clear_code`,
  `custom_components.rental_control.event_overrides.async_fire_update_times`,
  `custom_components.rental_control.event_overrides.get_event_identities`,
  and `custom_components.rental_control.coordinator.async_fire_clear_code`.
- **FR-007**: `async_fire_set_code` behavior MUST remain equivalent, including
  lock-name guard, prefix and trim-name handling, ownership verification, buffer
  application, date normalization, exact service-call sequence, gather-result
  handling, retry failure escalation, persistent-notification creation,
  expected-name confirmation wait, retry-success recording, notification
  dismissal, and `OperationResult` values.
- **FR-008**: `async_fire_clear_code` behavior MUST remain equivalent, including
  lock-name guard, expected-name ownership verification, reset button call,
  cancellation propagation, retry failure escalation, propagation delay, name and
  PIN state reads, unreadable-state handling, forced name clear, lingering-name
  and lingering-PIN classification, retry-success recording, notification
  dismissal, and `OperationResult` values.
- **FR-009**: `async_fire_update_times` behavior MUST remain equivalent,
  including slot and lock-name guard, ownership verification, buffer application,
  date normalization, end/start service-call ordering, gather-result handling,
  error classification, start/end confirmation waits, and `OperationResult`
  values.
- **FR-010**: `handle_state_change` behavior MUST remain equivalent, including
  coordinator and lock-name guards, settle delay, slot-number extraction,
  reset-entity handling, suppression feedback check, enabled-slot gate,
  code/name/date entity reads, empty and unreadable text-state handling,
  code-without-name protection, existing-override preservation during feedback,
  datetime parsing, trim/prefix full-name restoration, final
  `update_event_overrides` call, and the rule that callbacks do not launch
  reconciliation.
- **FR-011**: Empty and unreadable Keymaster-state helpers MUST preserve their
  current case-insensitive, whitespace-trimming semantics exactly because they
  are load-bearing for stateless self-heal and physical-slot safety.
- **FR-012**: `apply_buffer` MUST preserve its current behavior exactly,
  including returning original values when both buffers are zero, converting bare
  `date` values through coordinator timezone handling before arithmetic, and
  applying before and after minutes independently.
- **FR-013**: Event and slot naming helpers MUST preserve existing Airbnb, VRBO,
  Tripadvisor, Booking.com, Guesty API, Guesty, blocked/unavailable, prefix,
  date-normalization, UID-normalization, and degenerate fallback behavior.
- **FR-014**: Generic helper behavior MUST remain equivalent for gather-result
  checking, service-call collection, recursive folder deletion, entry-data
  lookup, UUID generation, early-expiry calculation, event-name extraction, and
  package-platform reload.
- **FR-015**: The completed decomposition MUST keep utility-related files below
  400 lines, project-owned functions below 80 lines, and project-owned parameter
  lists at no more than 6 parameters unless an external framework signature
  requires otherwise.
- **FR-016**: The implementation MUST NOT add an `aislop-ignore-file` directive
  to `util.py` or any replacement module to suppress file-size or
  function-length findings. Complexity findings must be resolved by
  behavior-preserving decomposition.
- **FR-017**: Decomposition MUST be by coherent concern, with distinct ownership
  for Keymaster service helpers, state-change handling, and generic helper
  behavior. Planning and implementation MAY decide exact module names and
  boundaries, but MUST NOT create a new ambiguous catch-all utility module.
- **FR-018**: Decomposition MUST NOT introduce blocking I/O, additional Home
  Assistant state writes, extra coordinator refreshes, new reconciliation
  launches, additional Keymaster service calls, or user-visible delays compared
  with the current implementation.
- **FR-019**: Planning and implementation documentation MUST state that this is a
  behavior-preserving refactor and MUST NOT define new lock-code business rules,
  new state-change semantics, new service calls, new sensors, new configuration
  options, or changed public caller behavior.

### Key Entities

- **Util Compatibility Surface**: The import boundary exposed by
  `custom_components.rental_control.util`. It remains source-compatible for
  production callers, direct tests, and monkeypatch targets while delegating to
  focused implementation units chosen in later stages.
- **Keymaster Service Helper**: A helper that mutates physical Keymaster slot
  entities for set, clear, or update-times operations and returns an
  `OperationResult` with confirmed, unconfirmed, failed, or lingering state.
- **State-Change Handler**: The Home Assistant callback that observes Keymaster
  entity changes and updates coordinator override state without launching
  reconciliation.
- **Keymaster Text State Helper**: The cleared, unreadable, and normalization
  logic for text entities whose exact semantics distinguish empty slots from
  unreadable physical state.
- **Buffered Access Window**: The start and end values after applying configured
  before and after minute buffers and date-to-datetime normalization.
- **Event Identity**: The name, start, end, and UID tuple used by event override
  and reconciliation behavior to identify calendar events without changing
  matching semantics.
- **Operation Result**: The public result object used by Keymaster service
  helpers and plan-application callers to classify physical operation outcomes
  without exposing raw PINs.

## Assumptions

- This specification covers issue #578's spec stage only; planning and
  implementation stages will decide exact module names, file layout, helper
  boundaries, compatibility mechanics, and any request objects.
- The live source at the time of this specification is a 1,173-line
  `custom_components/rental_control/util.py` with no `aislop-ignore-file`
  directive, one live file-size finding, four live function-length findings, and
  no function over the 6-parameter threshold.
- The heavy functions identified for decomposition are `handle_state_change`
  (204 lines), `async_fire_set_code` (174 lines), `async_fire_clear_code` (122
  lines), and `async_fire_update_times` (86 lines).
- `async_fire_set_code` owns slot display-name construction, buffer calculation,
  service-call sequencing, retry/error notification, and confirmation behavior.
- `async_fire_clear_code` owns reset dispatch, ownership safety, linger
  detection, force-clear behavior, retry/error notification, and confirmation
  behavior.
- `async_fire_update_times` owns buffered date-range writes and confirmation
  behavior.
- `handle_state_change` owns event validation, slot extraction, suppression,
  state snapshotting, text-state normalization, datetime parsing, trim/prefix
  restoration, and override-update dispatch.
- The existing source and tests are the behavior source of truth unless a later
  accepted issue explicitly changes utility behavior.
- Existing production callers include setup, coordinator, coordinator helper,
  sensor, calendar, config-flow, switch, listener, event-override, and
  event-override-helper paths. Existing tests directly import util names and
  patch Keymaster helper bindings on event-override and coordinator modules;
  hidden tests may patch the util compatibility surface directly.
- Runtime performance expectations are parity with the current implementation in
  normal Home Assistant operation, not a new user-visible performance feature.

## Non-Goals

- Changing Keymaster set, clear, update-times, ownership verification, retry,
  notification, lingering-state, or confirmation behavior.
- Changing `handle_state_change` callback semantics, suppression behavior,
  trim/prefix restoration, datetime parsing, or its no-reconciliation guarantee.
- Changing empty, unreadable, normalized Keymaster-state semantics or
  `apply_buffer` behavior.
- Adding new features, services, sensors, automations, reconciliation behavior,
  Store authority, configuration options, diagnostics fields, or recovery
  workflows.
- Changing the public `util.py` import surface consumed by current production
  callers, direct tests, or monkeypatch targets.
- Prescribing exact module names, file layout, class names, request-object
  shapes, or helper function signatures for the plan and implementation stages.
- Adding a new `aislop-ignore-file` directive for utility complexity findings.
- Closing issue #578 in this specification PR; later implementation work owns the
  runtime refactor.

## Constraints

- No behavior observable by Home Assistant users, automations, dashboards,
  services, logs relied on by tests, diagnostics consumers, physical Keymaster
  state, or existing tests may change as part of this refactor.
- The util compatibility boundary MUST remain importable and patchable for every
  currently consumed symbol, especially the Keymaster helper and event identity
  monkeypatch seams.
- Keymaster service helper ordering is safety-critical and MUST NOT be reordered,
  broadened, or optimized in a way that changes service calls, confirmation
  timing, retry state, or operation results.
- Empty and unreadable Keymaster-state helpers and `apply_buffer` are
  load-bearing for stateless self-heal and MUST be preserved exactly.
- The final implementation MUST satisfy the active file-size and function-length
  thresholds without adding a suppressing `aislop-ignore-file` directive.
- This specification stage is documentation-only and MUST NOT include production
  code changes.

## Security Considerations

- The utility service helpers directly control physical property access through
  Keymaster slot names, PIN programming, enable switches, date ranges, and reset
  buttons. Wrong ordering or wrong-slot behavior can lock out valid guests or
  leave stale access active.
- State-change handling can make occupied slots appear available or preserve
  stale access if unreadable, empty, code-without-name, trimmed-name, or
  suppressed-feedback semantics drift.
- Diagnostics, logs, operation results, and helper boundaries must continue to
  avoid exposing raw slot PINs beyond what existing Rental Control behavior
  already exposes.
- Behavior parity for self-heal helpers, buffer handling, operation results, and
  monkeypatch seams must be verified before any complexity improvement is
  considered successful.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of existing util-related unit and integration tests pass
  unchanged after the implementation stage completes, including `test_util.py`,
  relevant `test_event_overrides.py`, `test_coordinator.py`,
  `test_refresh_cycle.py`, `test_slot_concurrency.py`, sensor, calendar, and
  helper coverage.
- **SC-002**: In 100% of covered set, clear, and update-times scenarios, ordered
  Home Assistant service calls, service data, operation results, retry counters,
  notifications, confirmation waits, and state reads match the current
  implementation.
- **SC-003**: In 100% of covered state-change scenarios, reset handling,
  suppression, enabled/date-range gates, text-state normalization, datetime
  parsing, trim/prefix restoration, and `update_event_overrides` arguments match
  the current implementation.
- **SC-004**: In 100% of helper regression scenarios, cleared/unreadable text
  classification, `apply_buffer`, slot-name extraction, event identities,
  gathered-exception handling, cleanup, UUIDs, early expiry, and reload behavior
  match the current implementation.
- **SC-005**: All production modules and tests that currently import names from
  `custom_components.rental_control.util` continue to import those names without
  behavior changes or behavior-assertion rewrites.
- **SC-006**: The Keymaster helper and event identity monkeypatch targets remain
  patchable at `util.<name>` paths, and visible `event_overrides.*` and
  `coordinator.*` patch paths remain effective in 100% of existing tests.
- **SC-007**: The decomposed utility feature area contains no files of 400 lines
  or more, no project-owned functions of 80 lines or more, and no project-owned
  parameter lists over 6 parameters.
- **SC-008**: Active complexity checks pass without adding a utility
  `aislop-ignore-file` directive for file size or function length.
- **SC-009**: Normal service-helper and state-change processing performs no
  additional Home Assistant state writes, coordinator refreshes, reconciliation
  launches, blocking I/O, Keymaster service calls, or user-visible delays
  compared with the current implementation.
- **SC-010**: No production-code changes are included in this specification PR;
  the PR contains only the feature specification for the #578 decomposition
  pipeline.
