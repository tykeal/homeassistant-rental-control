<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Feature Specification: Decompose Event Overrides

**Feature Branch**: `017-decompose-event-overrides`
**Created**: 2026-06-28
**Status**: Draft
**Input**: User description: "Decompose
`custom_components/rental_control/event_overrides.py` for GitHub issue #575.
This is a behavior-preserving code-health refactor of the EventOverrides
slot-matching and override-application engine. Extract the three-phase matching
algorithm and plan-application helpers into focused, independently testable
units without changing runtime behavior."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Preserve Slot Matching Safety (Priority: P1)

As a property manager, I want each reservation to keep matching the same
physical Keymaster slot after decomposition, so upgrades cannot create duplicate
codes, clear the wrong slot, or assign a guest to the wrong slot.

**Why this priority**: `EventOverrides` owns the integration's most
algorithmically complex slot-matching behavior. Matching regressions have direct
physical-access impact and can reintroduce wrong-slot or duplicate-code
production bugs.

**Independent Test**: Can be fully tested by running the existing
event-override, slot-concurrency, refresh-cycle, coordinator, and utility tests
unchanged and by comparing match results for UID changes, overlapping stays,
same-start updates, trimmed names, prefixed names, duplicate names, and missing
calendar events before and after decomposition.

**Acceptance Scenarios**:

1. **Given** an incoming reservation and an existing slot have the same
   normalized non-empty UID and the same slot name, **When** a matching slot is
   requested, **Then** that UID-positive slot is selected before any time-window
   or trim-aware fallback candidate.
2. **Given** no UID-positive exact-name match exists, **When** a reservation's
   slot name overlaps an existing slot's time window, **Then** the name plus
   strict interval-overlap phase selects the same slot as the current engine.
3. **Given** overlapping same-name candidates contain different non-empty UIDs,
   **When** the candidate has the same start time and no other exact UID owner
   should win, **Then** the same-start bypass behavior introduced by PR #566 is
   preserved exactly.
4. **Given** Keymaster returns a stored display name that is trimmed, prefixed,
   or both, **When** the reservation is matched, **Then** the trim-aware and
   prefix-aware fallback behavior introduced by #624 and PR #625 selects the
   same slot and restores the same full name when the current engine would do so.
5. **Given** a slot fails to match current calendar events, **When** stale-slot
   cleanup evaluates it, **Then** the eviction tolerance counter increments,
   resets, clears, or preserves the slot with the same scope and thresholds as
   today.

---

### User Story 2 - Preserve Reconciliation Plan Application (Priority: P1)

As an existing Rental Control user, I want reconciliation plans to produce the
same Keymaster service calls, in-memory overrides, diagnostics, retry state, and
clear/set ordering after decomposition, so physical lock-code updates remain
safe during normal refreshes.

**Why this priority**: The reconciliation package computes desired slot plans,
but `EventOverrides` applies those plans to Keymaster and maintains the
coordinator-visible override state. Decomposition is safe only if every action
and side effect remains equivalent.

**Independent Test**: Can be fully tested by applying identical desired plans to
identical coordinator, reservation, actual-state, pending-fence, and Keymaster
state fixtures before and after decomposition and verifying the same ordered
operation results, service calls, diagnostics snapshots, retry counters, and
cached overrides.

**Acceptance Scenarios**:

1. **Given** a desired plan contains `clear`, `retry_clear`, `reset`, `set`,
   `assign`, `update_times`, `overwrite_manual_change`, `update_in_place`,
   `noop`, and `blocked` actions, **When** the plan is applied, **Then** the same
   actions are skipped, executed, logged, and appended to operation results in
   the same order as today.
2. **Given** a clear action requires preflight validation, **When** physical
   Keymaster state changed after planning or cannot be read, **Then** the same
   conservative unconfirmed result, pending-fence cleanup, and slot-preservation
   behavior is produced.
3. **Given** a clear operation succeeds, fails, remains unconfirmed, or leaves a
   lingering name or PIN, **When** the result is processed, **Then** pending
   fences, pending-clear slots, override state, UID state, miss counters, retry
   diagnostics, and last slot errors are updated exactly as before.
4. **Given** a set or assign action targets a slot, **When** that slot is not
   confirmed empty before programming, **Then** the same unconfirmed operation
   result is returned and no unsafe write occurs.
5. **Given** a set, update-times, or overwrite-manual-change action succeeds,
   fails, or becomes stale, **When** result handling completes, **Then**
   suppression markers, cached overrides, pending fences, diagnostics, and error
   state match the current side-effect ordering.
6. **Given** `async_apply_plan` exits normally or due to an exception, **When**
   callers inspect reconciliation activity and diagnostics, **Then**
   `reconciliation_active` and the diagnostics snapshot are finalized with the
   same timing and values as the current engine.

---

### User Story 3 - Preserve EventOverrides Compatibility Surface (Priority: P1)

As a Rental Control maintainer, I want the `EventOverrides` class and all
methods, properties, and compatibility fields consumed by production modules and
existing tests to remain available, so this refactor can be reviewed as a
behavior-preserving decomposition rather than a coordinated public API redesign.

**Why this priority**: The coordinator, helper shells, check-in runtime, utility
service helpers, and regression tests all call into `EventOverrides`. Changing
that surface would expand the blast radius beyond issue #575 and make behavior
parity harder to prove.

**Independent Test**: Can be fully tested by running existing tests unchanged
and by verifying all current production import sites and call sites continue to
load the same names, use the same call patterns, and observe the same results.

**Acceptance Scenarios**:

1. **Given** coordinator setup and config helpers construct `EventOverrides`,
   **When** setup, reload, and option-update paths run, **Then** construction and
   configuration through `trim_names`, `max_name_length`, and `event_prefix`
   remain compatible.
2. **Given** coordinator refresh and Store helpers consume override state,
   **When** refresh, adoption, plan application, diagnostics, and persistence
   paths run, **Then** `async_update`, `async_apply_plan`,
   `load_persisted_mappings`, `get_actual_state`, `update_actual_state`,
   `get_last_slot_error`, `diagnostics_snapshot`, `overrides`, and
   `suppress_state_changes` remain behavior-compatible.
3. **Given** utility service helpers perform Keymaster set, clear, and
   update-times operations, **When** they verify ownership, track retries, inspect
   existing overrides, or suppress feedback callbacks, **Then**
   `verify_slot_ownership`, `record_retry_failure`, `record_retry_success`,
   `should_suppress_state_change`, `async_update`, `overrides`, and the currently
   consumed escalation state remain compatible.
4. **Given** check-in runtime and sensor tests find slots by name, **When** they
   call slot lookup helpers, **Then** `get_slot_key_by_name`, `get_slot_name`,
   `get_slot_with_name`, and readiness properties retain their current behavior.
5. **Given** existing event-override and integration tests directly exercise
   compatibility helpers and private regression seams, **When** those tests run
   unchanged, **Then** the tested names remain available from `EventOverrides` or
   an equivalent compatibility boundary without rewriting behavior assertions.

---

### User Story 4 - Improve Maintainability Under Aislop Limits (Priority: P2)

As a maintainer, I want the oversized event override engine split into focused,
independently testable units, so future safety fixes can target matching,
stale-slot cleanup, and plan application without navigating one 1,864-line
module hidden behind a complexity suppression.

**Why this priority**: Issue #575 identifies `event_overrides.py` as the most
algorithmically complex module in the integration. Complexity reduction matters,
but only after behavior preservation is protected.

**Independent Test**: Can be fully tested by measuring the decomposed feature
area against active complexity thresholds and by adding focused tests for
extracted matching and plan-application behavior while existing tests continue
to pass unchanged.

**Acceptance Scenarios**:

1. **Given** the decomposition is complete, **When** complexity checks run,
   **Then** event-override-related files are below 400 lines, project-owned
   functions are below 80 lines, and project-owned parameter lists have no more
   than 6 parameters.
2. **Given** the matching behavior is tested independently, **When** inputs cover
   UID-positive matches, name plus overlap, same-start UID bypass, trim-aware
   fallback, prefixed names, duplicate names, and no-match cases, **Then** the
   selected slot and any restored full name match the current engine.
3. **Given** plan-application behavior is tested independently, **When** inputs
   cover clear, set, update-times, overwrite, stale-token, failed-operation, and
   unconfirmed-operation cases, **Then** operation results and side effects match
   the current engine.
4. **Given** the complexity suppression is removed, **When** linting runs,
   **Then** `event_overrides.py` no longer needs its temporary
   `complexity/file-too-large` or `complexity/function-too-long` suppression.

---

### Edge Cases

- What happens when a reservation UID changes while the start time remains the
  same? The same-start bypass introduced by PR #566 continues to treat the
  correct candidate as the same reservation only under the same conditions used
  today.
- What happens when an exact UID owner exists in another slot? UID-positive
  ownership continues to win before name/overlap or trim-aware fallback matching.
- What happens when two guests have the same display name or a trimmed display
  name collides with another name? Existing start-time, UID-owner, preferred-slot,
  and trim-aware disambiguation rules remain unchanged.
- What happens when the configured event prefix is present in stored Keymaster
  names? Prefix stripping and trim-length calculations continue to match the
  current `event_prefix`, `prefix_length`, `trim_names`, and `max_name_length`
  behavior.
- What happens when a calendar refresh temporarily omits a future reservation?
  Eviction tolerance counters remain scoped to the same slots and reset on the
  same successful match paths, preventing premature clears exactly as today.
- What happens when a stale, past, malformed, or beyond-boundary slot is checked
  by the retired greedy cleanup path? The same immediate clear, tolerant clear,
  or preservation decision is produced.
- What happens when a Keymaster command result is failed, unconfirmed, stale, or
  partially lingering? Pending fences, pending-clear bookkeeping, retry/error
  state, and cached override state remain conservative in the same way as today.
- What happens when persisted Store mappings are missing, stale, or reloaded?
  They remain cache-only and continue to integrate with coordinator and
  reconciliation behavior without changing slot correctness.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The decomposition MUST preserve all Home Assistant observable
  behavior of `EventOverrides`, including selected slots, Keymaster service
  helper calls, operation result ordering, cached override state, pending fences,
  pending-clear slots, retry counters, diagnostics snapshots, logging decisions
  relied on by tests, and coordinator-visible properties.
- **FR-002**: Existing event-override-related unit and integration tests MUST
  pass unchanged after the implementation stage; new tests MUST verify behavior
  parity and focused extracted behavior rather than introduce new runtime
  behavior.
- **FR-003**: The slot-matching algorithm MUST preserve the current three-phase
  ordering exactly: UID-positive exact-name match first, exact-name plus strict
  interval overlap with UID-aware same-start bypass second, and trim-aware
  fallback third.
- **FR-004**: UID-positive matching MUST continue to normalize UIDs, require the
  same exact-name condition for the first phase, bypass time-overlap checks in
  that phase, and take precedence over every name/overlap or trim-aware
  candidate.
- **FR-005**: Name plus overlap matching MUST continue to use the current strict
  interval rule, `start_a < end_b AND start_b < end_a`, and the same UID-owner,
  same-start bypass, preferred-slot, and exclude-slot semantics.
- **FR-006**: Same-start UID bypass behavior from PR #566 MUST be preserved
  exactly, including rejection when start times differ, preservation of exact UID
  owners, same-start preferred-slot tie-breaking, and mirrored behavior between
  matching a new event to a slot and checking whether a slot still has a matching
  event.
- **FR-007**: Trim-aware fallback behavior from #624 and PR #625 MUST be
  preserved exactly, including use of the `trim_name` utility together with
  configured `trim_names`, `max_name_length`, `event_prefix`, and
  `prefix_length`; UID-positive trim matching without overlap; trim matching
  with overlap; and restoration of a longer full name only when the current
  engine would restore it.
- **FR-008**: The mirror relationship between `_find_overlapping_slot` and
  `_slot_has_matching_event` MUST be preserved so both paths make equivalent
  decisions for UID-positive matches, name/overlap matches, same-start bypass,
  trim-aware fallback, and UID-owner exclusion.
- **FR-009**: Eviction tolerance counter behavior from PR #552 MUST be preserved
  exactly, including which slots increment miss counts, when miss counts reset,
  when the tolerance threshold triggers a clear, and when stale, past, malformed,
  empty-calendar, or beyond-boundary slots clear immediately.
- **FR-010**: `async_check_overrides` MUST remain a behavior-compatible retired
  greedy cleanup shim for existing tests and compatibility callers, even though
  production stale-slot cleanup is driven by reconciliation plans.
- **FR-011**: `async_apply_plan` MUST preserve plan-action dispatch semantics,
  including skipped `noop` and `blocked` actions, action-kind ordering, missing
  reservation handling, warning reasons, returned operation-result ordering,
  diagnostics snapshot updates, and `reconciliation_active` lifecycle.
- **FR-012**: Clear action application MUST preserve preflight-read behavior,
  operation fence tokens, pending-clear bookkeeping, expected-name ownership
  checks, confirmed-empty release behavior, failed and lingering-result error
  recording, and the rule that reconciliation clears do not update the retired
  greedy `next_slot` selector.
- **FR-013**: Set and assign action application MUST preserve confirmed-empty
  safety checks before programming, tentative in-memory assignment timing,
  service-feedback suppression markers, stale-token handling, failure rollback,
  error recording, and the rule that reconciliation sets do not update the
  retired greedy `next_slot` selector.
- **FR-014**: Update-times, overwrite-manual-change, and update-in-place action
  handling MUST preserve existing service calls, clear-before-replace ordering,
  drift logging without raw PIN exposure, cached start/end updates, suppression
  markers, and failure or unconfirmed-result behavior.
- **FR-015**: Integration with the reconciliation package MUST remain compatible,
  including consumption of `ActionKind`, `DesiredPlan`, `Reservation`, and
  `SlotAction`, and application of plans produced by the coordinator without
  caller-side behavior changes.
- **FR-016**: Integration with the coordinator MUST remain compatible, including
  construction during setup/config reload, actual-state observation, Store
  mapping loading, diagnostics exposure, refresh-cycle plan application,
  Keymaster service helper coordination, and check-in runtime slot lookup.
- **FR-017**: The production compatibility surface consumed by in-repository
  callers MUST remain source-compatible: `EventOverrides`, `overrides`,
  `ready`, `next_slot`, `trim_names`, `max_name_length`, `event_prefix`,
  `prefix_length`, `persisted_mappings`, `pending_clear_slots`,
  `pending_fences`, `reconciliation_active`, `diagnostics_snapshot`,
  `suppress_state_changes`, `should_suppress_state_change`,
  `get_last_slot_error`, `update_diagnostics_snapshot`,
  `load_persisted_mappings`, `update_actual_state`, `get_actual_state`,
  `release_pending_clear_slot`, `async_reserve_or_get_slot`, `async_update`,
  `verify_slot_ownership`, `record_retry_failure`, `record_retry_success`,
  `async_apply_plan`, `async_check_overrides`, `get_slot_name`,
  `get_slot_with_name`, `get_slot_key_by_name`, date/time getters, and `update`.
- **FR-018**: Existing tests that directly exercise current regression seams MUST
  continue to run unchanged, including tests for `_find_overlapping_slot`,
  `_slot_has_matching_event`, `_apply_clear`, `_apply_overwrite_manual_change`,
  `_record_slot_error`, `_overrides`, `_slot_uids`, `_slot_miss_counts`,
  `_pending_fences`, `_pending_clear_slots`, and the currently consumed
  escalation state.
- **FR-019**: The three functions currently over the 6-parameter threshold MUST
  be represented through project-owned call patterns that satisfy the active
  parameter-count rule while preserving observable behavior: the consumed greedy
  shim `async_reserve_or_get_slot`, the consumed public async update method
  `async_update`, and the consumed synchronous bootstrap/test compatibility
  method `update`.
- **FR-020**: The completed implementation MUST remove the file-level
  `aislop-ignore-file complexity/file-too-large complexity/function-too-long`
  directive from `event_overrides.py`; there is no hallucinated-import directive
  on this file to preserve or remove.
- **FR-021**: The completed decomposition MUST keep event-override-related files
  below 400 lines, project-owned functions below 80 lines, and project-owned
  parameter lists at no more than 6 parameters unless an external framework
  signature requires otherwise.
- **FR-022**: Decomposition MUST NOT introduce blocking I/O, additional
  coordinator refreshes, additional Home Assistant state writes, extra Store
  authority, or user-visible delays compared with the current implementation.
- **FR-023**: Planning and implementation documentation MUST state that this is a
  behavior-preserving refactor and MUST NOT define new lock-code business rules,
  new matching semantics, new reconciliation actions, new services, new sensors,
  new configuration options, or changed public caller behavior.

### Key Entities

- **EventOverrides Engine**: The coordinator-owned slot-matching and
  override-application object currently implemented by `EventOverrides`. It
  stores in-memory override state, UID mappings, pending fences, pending clear
  operations, retry/error state, and diagnostics while applying reconciliation
  plans to Keymaster.
- **Event Override**: A slot assignment snapshot containing the slot name, slot
  code, start time, and end time used for matching, cleanup, and Keymaster
  service helper calls.
- **Slot Match Decision**: The behavior-preserving decision that pairs an
  incoming reservation or current calendar event with an existing slot using the
  ordered UID-positive, name/overlap, and trim-aware fallback phases.
- **Same-Start UID Bypass**: The safety rule that allows a changed reservation
  with a regenerated UID to remain matched to the same slot when start times
  align and no exact UID owner should win.
- **Trim and Prefix Identity**: The configured identity behavior that handles
  Keymaster's stored trimmed display names and Rental Control event prefixes
  without changing reservation identity.
- **Eviction Tolerance State**: The per-slot miss-count bookkeeping that prevents
  transient calendar omissions from immediately clearing future reservations
  while still clearing stale or invalid slots at the same time as today.
- **Plan Application Result**: The ordered set of Keymaster clear, set,
  update-times, overwrite, retry, blocked, and no-op outcomes generated when a
  reconciliation `DesiredPlan` is applied.
- **Coordinator Compatibility Surface**: The class, methods, properties, and
  compatibility fields consumed by coordinator setup/config/store/refresh
  helpers, check-in runtime, utility service helpers, and regression tests.

## Assumptions

- This specification covers issue #575's spec stage only; planning and
  implementation stages will decide exact module names, file layout, helper
  boundaries, request objects, and compatibility mechanics.
- The live source at the time of this specification is a 1,864-line
  `custom_components/rental_control/event_overrides.py` with six functions over
  80 lines and three project-owned parameter lists over 6 parameters.
- The heavy functions identified for decomposition are `_find_overlapping_slot`,
  `async_check_overrides`, `_slot_has_matching_event`, `async_apply_plan`,
  `_apply_set`, and `_apply_clear`; the 7-parameter functions are
  `async_reserve_or_get_slot`, `async_update`, and `update`.
- The existing source and tests are the behavior source of truth unless a later
  accepted issue explicitly changes event-override behavior.
- Existing production callers are coordinator setup/config/store/refresh and
  reservation helper paths, check-in runtime slot lookup, and utility service
  helper paths; existing tests also directly exercise compatibility and private
  regression seams.
- Runtime performance expectations are parity with the current implementation in
  normal Home Assistant operation, not a new user-visible performance feature.

## Non-Goals

- Changing slot matching behavior, phase ordering, same-start UID bypass rules,
  trim-aware or prefix-aware matching, stale-slot cleanup, retry policy,
  diagnostics content, or Keymaster operation ordering.
- Adding new features, configuration options, reconciliation actions, services,
  sensors, automations, Store authority, or recovery workflows.
- Changing the public `EventOverrides` API or the compatibility surface consumed
  by current production callers and existing tests.
- Changing the reconciliation algorithm, coordinator refresh behavior, calendar
  parsing, check-in tracking behavior, or Keymaster service helper behavior
  beyond continuing to consume the same `EventOverrides` surface.
- Prescribing exact module names, file layout, class names, request-object
  shapes, or helper function signatures for the plan and implementation stages.
- Closing issue #575 in this specification PR; later implementation work owns
  the runtime refactor.

## Constraints

- No behavior observable by Home Assistant users, automations, dashboards,
  services, diagnostics consumers, logs relied on by tests, or existing tests may
  change as part of this refactor.
- The three-phase slot-matching order is correctness-critical and MUST NOT be
  reordered or broadened during decomposition.
- Same-start bypass behavior from PR #566, trim/prefix-aware matching from #624
  and PR #625, and eviction tolerance counter behavior from PR #552 MUST be
  preserved exactly.
- Integration with the reconciliation package and coordinator MUST be preserved;
  `EventOverrides` remains the public engine class used by those callers.
- The final implementation MUST remove the temporary
  `aislop-ignore-file complexity/file-too-large complexity/function-too-long`
  directive by satisfying the underlying thresholds.
- This specification stage is documentation-only and MUST NOT include production
  code changes.

## Security Considerations

- `EventOverrides` indirectly controls physical property access through
  Keymaster slot names, PIN programming, date ranges, clear operations, and
  reconciliation plan application. Incorrect matching or action ordering can
  lock out valid guests or leave stale access active for prior guests.
- Wrong-slot, duplicate-code, premature-clear, or stale-code regressions are
  safety failures. Behavior parity for matching and plan application must be
  verified before any complexity improvement is considered successful.
- Diagnostics, logs, Store mappings, and operation results must continue to avoid
  exposing raw slot PINs beyond what existing Rental Control behavior already
  exposes.
- Conservative handling of unreadable Keymaster states, unconfirmed clears,
  stale operation tokens, duplicate names, and active pending-clear fences is a
  safety requirement and must be preserved exactly.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of existing event-override-related unit and integration tests
  pass unchanged after the implementation stage completes, including
  `test_event_overrides.py`, `test_slot_concurrency.py`, relevant
  `test_refresh_cycle.py`, coordinator, sensor, and utility coverage.
- **SC-002**: In 100% of UID-positive, exact-name overlap, same-start bypass,
  trim-aware, prefix-aware, duplicate-name, and no-match regression scenarios,
  the decomposed matcher returns the same slot, no slot, or restored full name as
  the current engine.
- **SC-003**: In 100% of stale-slot cleanup scenarios, miss-count increments,
  resets, tolerance-threshold clears, immediate clears, and preserved slots match
  the current engine.
- **SC-004**: For identical reconciliation plans and coordinator state,
  `async_apply_plan` produces equivalent ordered operation results, service
  helper calls, cached override state, pending-fence state, retry/error state,
  diagnostics snapshots, and `reconciliation_active` transitions.
- **SC-005**: All production modules that currently construct or consume
  `EventOverrides` continue to run without behavior changes, and existing tests
  that import, construct, mock, or inspect it require no rewrite for behavior
  assertions.
- **SC-006**: The three current 7-parameter project-owned functions are brought
  under the active parameter-count threshold while preserving current consumed
  call behavior through compatibility or grouping decided in later stages.
- **SC-007**: The decomposed event-override feature area contains no files of 400
  lines or more, no project-owned functions of 80 lines or more, and no
  project-owned parameter lists over 6 parameters.
- **SC-008**: The temporary event-overrides complexity suppression is removed in
  the implementation stage, and active complexity checks pass without suppressing
  the decomposed slot-matching or plan-application behavior.
- **SC-009**: Normal refresh and plan-application processing performs no
  additional Home Assistant state writes, coordinator refreshes, blocking I/O,
  authoritative Store reads, or user-visible delays compared with the current
  implementation.
- **SC-010**: No production-code changes are included in this specification PR;
  the PR contains only the feature specification for the #575 decomposition
  pipeline.
