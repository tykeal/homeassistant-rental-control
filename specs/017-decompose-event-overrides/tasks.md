<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Tasks: Decompose Event Overrides

**Input**: Design documents from `/specs/017-decompose-event-overrides/`
**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅,
quickstart.md ✅

**Tests**: Included and required. This feature is a behavior-preserving
refactor of the 1,864-line `EventOverrides` engine, so existing event override,
slot concurrency, refresh-cycle, coordinator, sensor, and utility tests remain
the primary oracle. New focused tests prove parity for the shared matcher,
trim/prefix identity, eviction tolerance, reconciliation plan application,
diagnostics, compatibility wrappers, and caller import/usage boundaries.

**Organization**: Tasks are grouped by setup, foundational models, the ordered
helper split from PLAN, shell delegation, compatibility wrappers, maintainability,
and final gates. Implementation must keep
`custom_components/rental_control/event_overrides.py` as the public
`EventOverrides` shell while moving implementation detail into the internal
sibling `custom_components/rental_control/event_overrides_helpers/` package.

## Format: `- [ ] T### [P?] [Story?] Description with file path`

- **[P]**: Can run in parallel (different files, no dependency on incomplete
  tasks)
- **[Story]**: Which user story the task primarily proves (US1 through US4)
- Include exact file paths in descriptions
- Leave every checkbox unchecked until the implementation PR performs the task

## Path Conventions

- **Public shell**: `custom_components/rental_control/event_overrides.py`
- **Extracted package**:
  `custom_components/rental_control/event_overrides_helpers/`
- **Production callers that must keep imports and call styles unchanged**:
  `custom_components/rental_control/coordinator.py`,
  `custom_components/rental_control/coordinator_helpers/coordinator_config_shell.py`,
  `custom_components/rental_control/coordinator_helpers/coordinator_setup_shell.py`,
  `custom_components/rental_control/coordinator_helpers/coordinator_refresh_shell.py`,
  and `custom_components/rental_control/util.py`
- **Existing behavior-oracle tests**: `tests/unit/test_event_overrides.py`,
  `tests/unit/test_coordinator.py`, `tests/unit/test_sensors.py`,
  `tests/unit/test_util.py`, `tests/unit/test_keymaster_event_diagnostics.py`,
  `tests/integration/test_refresh_cycle.py`, and
  `tests/integration/test_slot_concurrency.py`
- **New focused tests**: `tests/unit/test_event_overrides_matcher.py` and
  `tests/unit/test_event_overrides_apply.py`
- **Feature docs**: `specs/017-decompose-event-overrides/`

## Live Module Transition Scope

Implementation changes the live event override engine only. The target module
split from PLAN is:

- `custom_components/rental_control/event_overrides.py` — public
  `EventOverrides` shell, `EventOverride`, `ReserveResult`, all FR-017 consumed
  members, locks, Home Assistant state reads, Keymaster service helper calls,
  in-memory state mutation, suppression state, actual-state cache, diagnostics
  storage, and private regression-seam wrappers.
- `custom_components/rental_control/event_overrides_helpers/__init__.py` —
  internal package marker and typed exports for helper tests only.
- `custom_components/rental_control/event_overrides_helpers/models.py` —
  `OverrideSnapshot`, `TrimConfig`, `MatchCatalog`, `MatchRequest`,
  `MatchResult`, `MatchPhase`, `SlotUpdateRequest`, `SlotReservationRequest`,
  `EvictionDecision`, and apply/diagnostic decision value types.
- `custom_components/rental_control/event_overrides_helpers/matcher.py` — one
  shared three-phase matcher used by both `_find_overlapping_slot` and
  `_slot_has_matching_event` in opposite orientations.
- `custom_components/rental_control/event_overrides_helpers/trim.py` —
  `_strip_prefix`, trim-name comparison, prefix-aware display-name handling, and
  restored-full-name decisions.
- `custom_components/rental_control/event_overrides_helpers/slot_bookkeeping.py`
  — sorted occupied/free slot ordering, next-slot selection, UID-owner checks,
  same-start preferred-slot selection, and request normalization helpers.
- `custom_components/rental_control/event_overrides_helpers/greedy_cleanup.py` —
  `async_check_overrides` decision helpers and eviction-tolerance miss-count
  actions for the retained retired greedy shim.
- `custom_components/rental_control/event_overrides_helpers/apply_dispatch.py` —
  pure `async_apply_plan` action classification, skip decisions, warning reasons,
  and result-ordering decisions.
- `custom_components/rental_control/event_overrides_helpers/apply_clear.py` —
  clear preflight and clear-result state mutation decisions.
- `custom_components/rental_control/event_overrides_helpers/apply_set.py` —
  set/assign operation tokens, confirmed-empty set decisions, suppression
  payloads, tentative assignment, stale-token handling, and rollback decisions.
- `custom_components/rental_control/event_overrides_helpers/apply_update.py` —
  update-times, overwrite-manual-change, update-in-place, drift logging, and
  clear-before-replace decisions.
- `custom_components/rental_control/event_overrides_helpers/diagnostics.py` —
  diagnostics snapshot projection without raw PIN exposure.

No production caller may import from `event_overrides_helpers/`. The stable
public import `from .event_overrides import EventOverrides` or
`from ..event_overrides import EventOverrides` must remain the only production
boundary.

---

## Phase 1: Setup & Baseline (Shared Infrastructure)

**Purpose**: Establish behavior, import, call-site, complexity, and hot-path
baselines before moving any production code.

- [X] T001 Run `.specify/scripts/bash/check-prerequisites.sh --json` with `SPECIFY_FEATURE=017-decompose-event-overrides` from the repository root and confirm `specs/017-decompose-event-overrides/` reports `research.md`, `data-model.md`, and `quickstart.md`
- [X] T002 Inspect US1-US4, FR-001 through FR-023, constraints, security considerations, and SC-001 through SC-010 in `specs/017-decompose-event-overrides/spec.md`
- [X] T003 Inspect the Project Structure, Concrete Decomposition Design, shared matcher, plan application split, parameter-count strategy, and `aislop` directive removal sections in `specs/017-decompose-event-overrides/plan.md`
- [X] T004 Inspect all research decisions, helper entities, request objects, and quickstart parity commands in `specs/017-decompose-event-overrides/research.md`, `specs/017-decompose-event-overrides/data-model.md`, and `specs/017-decompose-event-overrides/quickstart.md`
- [X] T005 Inventory `_find_overlapping_slot`, `_slot_has_matching_event`, `_event_has_other_uid_owner`, `_slot_has_other_uid_owner`, `_get_same_start_uid_bypass_slot`, `async_check_overrides`, `async_apply_plan`, `_apply_clear`, `_apply_set`, `_apply_update_times`, `_apply_overwrite_manual_change`, `async_reserve_or_get_slot`, `async_update`, `verify_slot_ownership`, diagnostics methods, and `update` in `custom_components/rental_control/event_overrides.py`
- [X] T006 Inventory production caller imports and call styles in `custom_components/rental_control/coordinator.py`, `custom_components/rental_control/coordinator_helpers/coordinator_config_shell.py`, `custom_components/rental_control/coordinator_helpers/coordinator_setup_shell.py`, `custom_components/rental_control/coordinator_helpers/coordinator_refresh_shell.py`, and `custom_components/rental_control/util.py`, recording that no caller import or usage may change
- [X] T007 Inventory existing event override, slot concurrency, refresh-cycle, coordinator, sensor, utility, and diagnostics behavior coverage in `tests/unit/test_event_overrides.py`, `tests/unit/test_coordinator.py`, `tests/unit/test_sensors.py`, `tests/unit/test_util.py`, `tests/unit/test_keymaster_event_diagnostics.py`, `tests/integration/test_refresh_cycle.py`, and `tests/integration/test_slot_concurrency.py`
- [X] T008 Run unchanged baseline event-override parity tests with `uv run pytest tests/unit/test_event_overrides.py tests/integration/test_slot_concurrency.py -q` against the listed test files
- [X] T009 Run unchanged baseline caller coverage with `uv run pytest tests/unit/test_coordinator.py tests/unit/test_sensors.py tests/unit/test_util.py tests/unit/test_keymaster_event_diagnostics.py tests/integration/test_refresh_cycle.py -q` against the listed test files
- [X] T010 Record the current line, function-length, and parameter-count baseline for `custom_components/rental_control/event_overrides.py`, including the exact `# aislop-ignore-file complexity/file-too-large complexity/function-too-long` directive and that there is no hallucinated-import directive on this file
- [X] T011 Record the current Home Assistant state-read, Keymaster service-call, Store-neutral cache, and coordinator refresh boundaries in `custom_components/rental_control/event_overrides.py` so helper extraction can prove no new hot-path I/O or user-visible delays

---

## Phase 2: Foundational Package and Models (Blocking Prerequisites)

**Purpose**: Create the internal package and behavior-free dataclasses before
any helper module imports them.

**⚠️ CRITICAL**: No matcher, trim, cleanup, plan-application, diagnostics, or
wrapper extraction can complete until model ownership is stable. Model modules
must not import Home Assistant APIs, Store APIs, coordinator code, or Keymaster
service helpers.

### Foundational Tests

- [X] T012 [US4] Add internal helper package import and model construction tests for `OverrideSnapshot`, `TrimConfig`, `MatchCatalog`, `MatchRequest`, `MatchResult`, `MatchPhase`, `SlotUpdateRequest`, `SlotReservationRequest`, and `EvictionDecision` in `tests/unit/test_event_overrides_matcher.py`
- [X] T013 [US2] Add apply decision model construction tests for dispatch, clear, set, update, overwrite, suppression, and diagnostics value types in `tests/unit/test_event_overrides_apply.py`
- [X] T014 [US4] Add tests proving `custom_components/rental_control/event_overrides_helpers/models.py` is side-effect-free and does not import Home Assistant, Store, coordinator, or Keymaster service modules in `tests/unit/test_event_overrides_matcher.py`

### Foundational Implementation

- [X] T015 Create `custom_components/rental_control/event_overrides_helpers/__init__.py` with SPDX headers, a module docstring, internal typed exports, and no production caller dependency
- [X] T016 [US4] Create `custom_components/rental_control/event_overrides_helpers/models.py` with `OverrideSnapshot`, `TrimConfig`, `MatchCatalog`, `MatchRequest`, `MatchResult`, and `MatchPhase` covering the PLAN fields while keeping enum values internal
- [X] T017 [US3] Add `SlotUpdateRequest` and `SlotReservationRequest` to `custom_components/rental_control/event_overrides_helpers/models.py` with fields matching the real `async_update`, `update`, and `async_reserve_or_get_slot` call styles
- [X] T018 [US1] Add `EvictionDecision` and miss-count action value types to `custom_components/rental_control/event_overrides_helpers/models.py` for stale-slot cleanup parity
- [X] T019 [US2] Add plan-application and diagnostics decision value types to `custom_components/rental_control/event_overrides_helpers/models.py` without carrying raw PIN values into diagnostics fields
- [X] T020 [US4] Ensure every model and helper-owned dataclass initializer in `custom_components/rental_control/event_overrides_helpers/models.py` has no more than six explicit parameters unless an external framework signature requires otherwise
- [X] T021 Run foundational validation with `uv run pytest tests/unit/test_event_overrides_matcher.py tests/unit/test_event_overrides_apply.py -q` against the new model tests

**Checkpoint**: The internal package exists, shared dataclasses are stable,
helper modules can import them, and FR-019/FR-021 parameter limits are accounted
for before behavior moves.

---

## Phase 3: Shared Three-Phase Matcher (Priority: P1) 🎯 MVP

**Goal**: Preserve slot matching safety by extracting one shared phase
implementation for both mirror methods while retaining their intentional return
shape difference.

**Independent Test**: Compare helper and shell results for identical override
snapshots, UID maps, trim config, events, excluded slots, duplicate names, and
stored names. `_find_overlapping_slot` returns the selected slot for one
incoming event; `_slot_has_matching_event` returns true only when that same
shared phase implementation selects the checked slot for any current event.

### Tests for Shared Matcher

> **NOTE: Add focused parity tests first. Existing `tests/unit/test_event_overrides.py`
> behavior assertions must remain unchanged.**

- [X] T022 [US1] Add matcher fixture builders that serialize current `EventOverrides` state into `OverrideSnapshot`, `TrimConfig`, and `MatchCatalog` inputs in `tests/unit/test_event_overrides_matcher.py`
- [X] T023 [US1] Add UID-positive exact-name matcher tests proving normalized non-empty UID plus exact slot name wins before overlap or trim fallback and does not require time overlap in `tests/unit/test_event_overrides_matcher.py`
- [X] T024 [US1] Add exact-name strict-overlap tests proving `start_a < end_b AND start_b < end_a`, non-overlap misses, `exclude_slot`, UID-owner exclusion, and preferred-slot tie-breaking in `tests/unit/test_event_overrides_matcher.py`
- [X] T025 [US1] Add same-start UID bypass tests proving PR #566 behavior for same UTC start acceptance, different-start rejection, exact UID owner precedence, preferred same-start slot selection, and duplicate-name disambiguation in `tests/unit/test_event_overrides_matcher.py`
- [X] T026 [US1] Add mirror-consistency tests proving `_find_overlapping_slot` and `_slot_has_matching_event` use equivalent UID-positive, name/overlap, PR #566 same-start bypass, #624/#625 trim-aware, and UID-owner exclusion semantics while preserving return-shape differences in `tests/unit/test_event_overrides_matcher.py`

### Implementation for Shared Matcher

- [X] T027 [US1] Implement `custom_components/rental_control/event_overrides_helpers/matcher.py` with `build_match_catalog()`, `find_uid_positive_exact_name()`, `find_exact_name_strict_overlap()`, `find_trim_aware_fallback()`, and a shared matcher dispatcher using `MatchCatalog` and `MatchRequest`
- [X] T028 [US1] Preserve UID normalization, occupied-slot ordering, `exclude_slot`, exact-name precedence, strict-overlap comparison, same-start preferred-slot selection, exact UID owner precedence, and restored-name reporting in `custom_components/rental_control/event_overrides_helpers/matcher.py`
- [X] T029 [US1] Verify `custom_components/rental_control/event_overrides_helpers/matcher.py` performs only in-memory matching and does not call Home Assistant APIs, Store APIs, Keymaster service helpers, `async_request_refresh()`, or mutate `EventOverrides` state directly
- [X] T030 [US1] Run matcher validation with `uv run pytest tests/unit/test_event_overrides_matcher.py tests/unit/test_event_overrides.py -q` against the listed test files

**Checkpoint**: Shared matcher helper behavior proves FR-003, FR-004, FR-005,
FR-006, FR-007, FR-008, SC-002, and the matching portions of SC-005 without yet
changing production caller imports.

---

## Phase 4: Trim, Slot Bookkeeping, and Greedy Cleanup (Priority: P1)

**Goal**: Preserve trim/prefix identity, next-slot bookkeeping, UID ownership,
same-start helper behavior, and retired greedy cleanup including eviction
tolerance counters.

**Independent Test**: Run existing trim, ownership, reserve/update, and stale
cleanup tests unchanged plus focused helper tests for prefix stripping, restored
full names, UID-owner checks, same-start selection, miss-count increments,
resets, tolerance-threshold clears, and immediate stale clears.

### Tests for Trim, Bookkeeping, and Cleanup

- [X] T031 [US1] Add focused trim helper tests for `_strip_prefix`, `trim_name` delegation, #624/#625 guest maximum calculation, prefix-aware comparison, and restored-full-name decisions in `tests/unit/test_event_overrides_matcher.py`
- [X] T032 [US1] Add slot bookkeeping tests for sorted occupied slots, sorted free slots, retired greedy next-slot selection, UID owner lookup, same-start preferred-slot tie-breaking, and `exclude_slot` handling in `tests/unit/test_event_overrides_matcher.py`
- [X] T033 [US1] Add greedy cleanup helper tests for PR #552 matched-slot miss-count reset, future missing-slot increment, `SLOT_MISS_THRESHOLD` clear, empty-calendar clear, malformed-window clear, past clear, beyond-boundary clear, and conservative failed/unconfirmed clear preservation in `tests/unit/test_event_overrides_matcher.py`

### Implementation for Trim, Bookkeeping, and Cleanup

- [X] T034 [US1] Implement `custom_components/rental_control/event_overrides_helpers/trim.py` with prefix stripping, `TrimConfig` construction, trim-name comparison via the existing `trim_name` behavior, and restored-full-name decisions
- [X] T035 [US1] Implement `custom_components/rental_control/event_overrides_helpers/slot_bookkeeping.py` with sorted occupied/free slot helpers, next-slot selection, UID-owner checks, and same-start preferred-slot selection used by matcher and shell wrappers
- [X] T036 [US1] Implement `custom_components/rental_control/event_overrides_helpers/greedy_cleanup.py` with pure `EvictionDecision` production for `async_check_overrides` miss-count resets, increments, threshold clears, immediate stale clears, and preserve decisions
- [X] T037 [US1] Wire `async_check_overrides` in `custom_components/rental_control/event_overrides.py` to apply `greedy_cleanup.py` decisions while retaining the lock boundary, `async_fire_clear_code`, failed/unconfirmed/lingering preservation, and `__assign_next_slot()` only for retired greedy clears
- [X] T038 [US1] Verify `trim.py`, `slot_bookkeeping.py`, and `greedy_cleanup.py` perform no Home Assistant state reads, Store writes, coordinator refresh requests, or Keymaster service calls
- [X] T039 [US1] Run cleanup validation with `uv run pytest tests/unit/test_event_overrides_matcher.py tests/unit/test_event_overrides.py -q` against the listed test files

**Checkpoint**: Trim and cleanup prove FR-007, FR-009, FR-010, FR-022, SC-003,
and the stale-slot portions of SC-005 while preserving the retired greedy shim.

---

## Phase 5: Reconciliation Plan Application Helpers (Priority: P1)

**Goal**: Preserve `async_apply_plan`, clear, set, update-times, overwrite, and
update-in-place behavior while moving pure decisions into focused helpers.

**Independent Test**: Apply identical desired plans to identical coordinator,
reservation, actual-state, pending-fence, pending-clear, and Keymaster fixtures
and compare ordered operation results, service calls, diagnostics snapshots,
retry/error state, cached overrides, and side-effect ordering.

### Tests for Plan Application

- [X] T040 [US2] Add dispatch tests for `NOOP`, `BLOCKED`, `CLEAR`, `RETRY_CLEAR`, `RESET`, `SET`, `ASSIGN`, `UPDATE_TIMES`, `OVERWRITE_MANUAL_CHANGE`, `UPDATE_IN_PLACE`, missing reservations, warning reasons, result ordering, and ignored unknown actions in `tests/unit/test_event_overrides_apply.py`
- [X] T041 [US2] Add clear preflight and clear-result tests for fresh state reads, unreadable state, changed physical state, confirmed-empty release, operation fences, pending-clear cleanup, failed clear errors, lingering name/PIN errors, stale tokens, and no retired-greedy next-slot update in `tests/unit/test_event_overrides_apply.py`
- [X] T042 [US2] Add set and assign tests for confirmed-empty checks before programming, no unsafe write on occupied physical state, tentative assignment timing, suppression payloads, stale-token handling, failure rollback, unconfirmed preservation, and no retired-greedy next-slot update in `tests/unit/test_event_overrides_apply.py`
- [X] T043 [US2] Add update-times tests for service-call arguments, suppression markers, confirmed cached buffered start/end updates, failed or unconfirmed result behavior, retry/error state, and operation-result parity in `tests/unit/test_event_overrides_apply.py`
- [X] T044 [US2] Add overwrite-manual-change and update-in-place tests for drift logging without raw PINs, clear-before-replace ordering, skipped replacement when clear is not confirmed, replacement set plan-id generation, and side-effect ordering in `tests/unit/test_event_overrides_apply.py`
- [X] T045 [US2] Add `async_apply_plan` lifecycle tests proving `reconciliation_active` becomes true before dispatch, diagnostics update in `finally`, false is set under the lock, and exceptions preserve current finalization behavior in `tests/unit/test_event_overrides_apply.py`

### Implementation for Plan Application

- [X] T046 [US2] Implement `custom_components/rental_control/event_overrides_helpers/apply_dispatch.py` with pure action classification, skip decisions, missing-reservation handling, clear warning reasons, and operation-result ordering decisions
- [X] T047 [US2] Implement `custom_components/rental_control/event_overrides_helpers/apply_clear.py` with clear preflight and clear-result decisions while leaving operation fences, fresh Home Assistant reads, Keymaster clear calls, and shell state mutation ordering in `event_overrides.py`
- [X] T048 [US2] Implement `custom_components/rental_control/event_overrides_helpers/apply_set.py` with deterministic set operation IDs, confirmed-empty set decisions, tentative override payloads, feedback suppression payloads, stale-token handling, failure rollback, and unconfirmed preservation decisions
- [X] T049 [US2] Implement `custom_components/rental_control/event_overrides_helpers/apply_update.py` with update-times suppression decisions, cached start/end updates, overwrite drift fields, clear-before-replace decisions, failed/unconfirmed short-circuiting, and replacement set plan-id decisions
- [X] T050 [US2] Wire `async_apply_plan`, `_apply_clear`, `_apply_set`, `_apply_update_times`, and `_apply_overwrite_manual_change` in `custom_components/rental_control/event_overrides.py` to the apply helpers while preserving clear preflight reads, confirmed-empty set checks, Keymaster service helper call order, pending fences, pending-clear state, suppression markers, retry/error state, cached overrides, and returned operation-result ordering byte-for-byte
- [X] T051 [US2] Verify `apply_dispatch.py`, `apply_clear.py`, `apply_set.py`, and `apply_update.py` do not call Home Assistant APIs, Store APIs, Keymaster service helpers, `async_request_refresh()`, or mutate `EventOverrides` state directly
- [X] T052 [US2] Run plan-application validation with `uv run pytest tests/unit/test_event_overrides_apply.py tests/unit/test_event_overrides.py tests/integration/test_refresh_cycle.py -q` against the listed test files

**Checkpoint**: Plan application proves FR-011, FR-012, FR-013, FR-014,
FR-015, FR-016, FR-022, SC-004, and reconciliation-package integration without
changing caller behavior.

---

## Phase 6: Diagnostics Projection (Priority: P1)

**Goal**: Preserve diagnostics snapshot keys, values, sorting, retry-count
ranges, pending-clear slots, manual drift fields, last-slot errors, and raw PIN
redaction while moving pure projection out of the shell.

**Independent Test**: Compare `diagnostics_snapshot` for identical desired plans,
pending state, retry counters, last errors, and manual-drift plans before and
after extraction.

### Tests for Diagnostics

- [X] T053 [US2] Add diagnostics projection tests for matched slots, pending corrections, manual drift slots, pending clear slots, slot retry counts, last slot errors, enum string values, sorting, and raw PIN redaction in `tests/unit/test_event_overrides_apply.py`

### Implementation for Diagnostics

- [X] T054 [US2] Implement `custom_components/rental_control/event_overrides_helpers/diagnostics.py` with pure diagnostics snapshot projection from `DesiredPlan`, retry/error state, pending clear state, and configured slot range
- [X] T055 [US2] Wire `update_diagnostics_snapshot` in `custom_components/rental_control/event_overrides.py` to `diagnostics.py` while storing the returned dict at the same point in `async_apply_plan`'s `finally` block
- [X] T056 [US2] Verify `custom_components/rental_control/event_overrides_helpers/diagnostics.py` never includes raw slot codes and performs no Home Assistant, Store, Keymaster, or coordinator refresh side effects
- [X] T057 [US2] Run diagnostics validation with `uv run pytest tests/unit/test_event_overrides_apply.py tests/unit/test_keymaster_event_diagnostics.py -q` against the listed test files

**Checkpoint**: Diagnostics extraction proves FR-001, FR-011, FR-014, FR-017,
SC-004, and the raw-PIN security requirement.

---

## Phase 7: Shell Delegation and Public Compatibility (Priority: P1)

**Goal**: Reduce `event_overrides.py` to the public shell while keeping every
FR-017 consumed member and FR-018 private regression seam available with
unchanged behavior.

**Independent Test**: Run existing tests unchanged and prove production callers
continue importing `EventOverrides` from `event_overrides.py`, while both mirror
wrappers delegate to the one shared matcher.

### Tests for Shell Delegation

- [X] T058 [US1] Add shell delegation tests proving `_find_overlapping_slot` and `_slot_has_matching_event` both build a shared `MatchCatalog`, call the same matcher phase implementation in opposite orientations, and apply restored full-name mutations only when the current engine would in `tests/unit/test_event_overrides_matcher.py`
- [X] T059 [US3] Add compatibility-surface tests proving `EventOverrides`, `EventOverride`, `ReserveResult`, all FR-017 members, and FR-018 private regression seams remain available from `custom_components.rental_control.event_overrides` in `tests/unit/test_event_overrides.py`
- [X] T060 [US3] Add production import-boundary tests proving coordinator setup/config helpers still use `from ..event_overrides import EventOverrides` and no production module imports from `event_overrides_helpers/` in `tests/unit/test_event_overrides.py`

### Implementation for Shell Delegation

- [X] T061 [US1] Wire `_find_overlapping_slot` in `custom_components/rental_control/event_overrides.py` to build `MatchCatalog`, call the shared matcher, return the selected slot, and apply restored full-name mutation at the same point as today
- [X] T062 [US1] Wire `_slot_has_matching_event` in `custom_components/rental_control/event_overrides.py` to evaluate events through the same shared matcher and return true only when the shared result selects the checked slot
- [X] T063 [US3] Keep `EventOverrides`, `EventOverride`, `ReserveResult`, properties, date/time getters, `verify_slot_ownership`, retry helpers, suppression helpers, actual-state helpers, diagnostics helpers, `_apply_*` wrappers, and consumed private state reachable from `custom_components/rental_control/event_overrides.py`
- [X] T064 [US3] Verify reconciliation integration by confirming `custom_components/rental_control/event_overrides.py` and `event_overrides_helpers/*.py` continue consuming `ActionKind`, `DesiredPlan`, `Reservation`, and `SlotAction` from `custom_components/rental_control/reconciliation/` without caller-side behavior changes
- [X] T065 [US3] Run shell compatibility validation with `uv run pytest tests/unit/test_event_overrides_matcher.py tests/unit/test_event_overrides.py tests/unit/test_coordinator.py tests/unit/test_sensors.py tests/integration/test_refresh_cycle.py tests/integration/test_slot_concurrency.py -q` against the listed test files

**Checkpoint**: Shell delegation proves FR-001, FR-002, FR-008, FR-015,
FR-016, FR-017, FR-018, SC-001, SC-002, and SC-005 with the mirror wrappers
sharing one matcher implementation.

---

## Phase 8: Parameter Reduction and Caller Compatibility (Priority: P1)

**Goal**: Replace the three 7-parameter project-owned signatures with request
objects and thin compatibility wrappers while preserving every real call style.

**Independent Test**: Pin all accepted call forms before changing signatures,
then verify production call sites and existing tests remain source-compatible.

### Tests for Wrapper Compatibility

- [X] T066 [US3] Add `async_reserve_or_get_slot` compatibility tests for `SlotReservationRequest`, all keyword fields, four positional values plus `uid=`, optional `prefix=`, unknown keyword rejection, and current `ReserveResult` semantics in `tests/unit/test_event_overrides.py`
- [X] T067 [US3] Add `async_update` compatibility tests for `SlotUpdateRequest`, coordinator's six-positional `(slot, code, name, start, end, prefix)` form, util's five-positional reset `(slot_num, "", "", start, start)` form, keyword forms, duplicate redirect with `exclude_slot`, prefix stripping, and unknown keyword rejection in `tests/unit/test_event_overrides.py`
- [X] T068 [US3] Add synchronous `update` compatibility tests for `SlotUpdateRequest`, five positional values, keyword fields, `prefix=` cases, copy-on-write behavior, empty-name clearing, next-slot reassignment, and unknown keyword rejection in `tests/unit/test_event_overrides.py`

### Implementation for Wrapper Compatibility

- [X] T069 [US3] Implement request normalizers in `custom_components/rental_control/event_overrides_helpers/slot_bookkeeping.py` or `models.py` for `SlotReservationRequest` and `SlotUpdateRequest`, preserving accepted legacy positional and keyword forms while failing unknown keywords fast
- [X] T070 [US3] Change `async_reserve_or_get_slot` in `custom_components/rental_control/event_overrides.py` to a thin no-more-than-six-parameter compatibility wrapper accepting a request object or legacy call form while preserving lock acquisition, prefix stripping, matcher delegation, miss-count reset, UID recording, next-slot assignment, and `ReserveResult` behavior
- [X] T071 [US3] Change `async_update` in `custom_components/rental_control/event_overrides.py` to a thin no-more-than-six-parameter compatibility wrapper accepting `update=None, *values, **legacy`, including the coordinator six-positional form, util reset form, request object, and keyword forms while preserving duplicate redirect, prefix stripping, state mutation, miss-count reset, `__assign_next_slot()`, and readiness behavior
- [X] T072 [US3] Change synchronous `update` in `custom_components/rental_control/event_overrides.py` to a thin no-more-than-six-parameter compatibility wrapper accepting the request object, five positional form, keyword forms, and `prefix=` while preserving copy-on-write assignment and readiness behavior
- [X] T073 [US3] Verify no caller import or usage changes in `custom_components/rental_control/util.py` (`verify_slot_ownership` at clear/set/update-times helpers and reset `async_update`), `custom_components/rental_control/coordinator.py` (`async_update`), `custom_components/rental_control/coordinator_helpers/coordinator_refresh_shell.py` (`async_apply_plan`), setup/config shells (`EventOverrides` import), and existing tests
- [X] T074 [US3] Run wrapper and caller validation with `uv run pytest tests/unit/test_event_overrides.py tests/unit/test_util.py tests/unit/test_coordinator.py tests/unit/test_sensors.py tests/integration/test_refresh_cycle.py -q` against the listed test files

**Checkpoint**: Parameter reduction proves FR-017, FR-018, FR-019, FR-015,
FR-016, SC-005, and SC-006 while preserving all real production and test call
forms.

---

## Phase 9: Maintainability, File Sizes, and Directive Removal (Priority: P2)

**Goal**: Ensure the decomposed event-override feature area satisfies active
`aislop` limits, removes temporary extraction shims, and removes only the
intended complexity suppression.

**Independent Test**: Measure every in-scope file immediately before directive
removal and run `aislop` after removal without replacement complexity ignores.

### Tests and Cleanup for Maintainability

- [X] T075 [US4] Confirm final implementation diff is limited to `custom_components/rental_control/event_overrides.py`, `custom_components/rental_control/event_overrides_helpers/`, `tests/unit/test_event_overrides.py`, `tests/unit/test_event_overrides_matcher.py`, `tests/unit/test_event_overrides_apply.py`, and directly required existing caller test files
- [X] T076 [US4] Remove temporary extraction shims from `custom_components/rental_control/event_overrides.py` and `custom_components/rental_control/event_overrides_helpers/*.py`, leaving only planned public class members, private regression-seam wrappers, and internal helper exports
- [X] T077 [US4] Ensure every project-owned function in `custom_components/rental_control/event_overrides.py` and `custom_components/rental_control/event_overrides_helpers/*.py` is below 80 lines, splitting helper functions without changing behavior where needed
- [X] T078 [US4] Ensure every project-owned parameter list in `custom_components/rental_control/event_overrides.py` and `custom_components/rental_control/event_overrides_helpers/*.py` has no more than six parameters, with `async_reserve_or_get_slot`, `async_update`, and `update` covered by request wrappers
- [X] T079 [US4] Immediately before removing the complexity directive, measure `custom_components/rental_control/event_overrides.py` and every `custom_components/rental_control/event_overrides_helpers/*.py` file with `wc -l` and confirm each file is below 400 lines
- [X] T080 [US4] Run isolated complexity validation with `uv run pre-commit run aislop` against the staged event-override implementation files and confirm file-size, function-length, and parameter-count thresholds pass
- [X] T081 [US4] Remove the `# aislop-ignore-file complexity/file-too-large complexity/function-too-long` directive from `custom_components/rental_control/event_overrides.py` after T079 and T080 pass, and do not add any replacement complexity suppression or hallucinated-import directive
- [X] T082 [US4] Re-run `uv run pre-commit run aislop` after directive removal and confirm all in-scope event-override files still pass active thresholds
- [X] T083 [US4] Confirm no new matching semantics, reconciliation actions, services, sensors, configuration options, Store authority, Home Assistant state writes, coordinator refreshes, blocking I/O, or user-visible delays were introduced in `custom_components/rental_control/event_overrides.py` or `event_overrides_helpers/*.py`

**Checkpoint**: Maintainability proves FR-020, FR-021, FR-022, FR-023, SC-007,
SC-008, SC-009, and the implementation-stage complexity goals.

---

## Phase 10: Polish & Cross-Cutting Acceptance Gates

**Purpose**: Verify full behavior parity, caller compatibility, quality gates,
traceability, and docs-only stage boundaries.

### Acceptance and Quality Gates

- [X] T084 Run unchanged event-override parity tests with `uv run pytest tests/unit/test_event_overrides.py tests/integration/test_slot_concurrency.py -q` against the listed test files
- [X] T085 Run all new focused helper tests with `uv run pytest tests/unit/test_event_overrides_matcher.py tests/unit/test_event_overrides_apply.py -q` against the listed test files
- [X] T086 Run unchanged caller and integration coverage with `uv run pytest tests/unit/test_coordinator.py tests/unit/test_sensors.py tests/unit/test_util.py tests/unit/test_keymaster_event_diagnostics.py tests/integration/test_refresh_cycle.py -q` against the listed test files
- [X] T087 Run full regression tests with `uv run pytest tests/ -x -q` against `tests/`
- [X] T088 Run linting with `uv run ruff check custom_components/ tests/` against `custom_components/` and `tests/`
- [X] T089 Run full pre-commit validation with `uv run pre-commit run --all-files` against repository-tracked files, including reuse, yamllint, actionlint, aislop, ruff, ruff-format, mypy, interrogate, and gitlint hooks
- [X] T090 Verify every FR-001 through FR-023 has a test, implementation, or acceptance task mapped in `specs/017-decompose-event-overrides/tasks.md`
- [X] T091 Verify every SC-001 through SC-010 has a test, implementation, or acceptance task mapped in `specs/017-decompose-event-overrides/tasks.md`
- [X] T092 Review `specs/017-decompose-event-overrides/quickstart.md` and confirm the implementation PR notes list unchanged parity commands, new focused matcher/apply commands, mirror-consistency coverage, wrapper compatibility forms, caller-import verification, hot-path safeguards, file-size measurement before directive removal, final `aislop` results, and final validation results

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup & Baseline (Phase 1)**: No dependencies.
- **Foundational Package and Models (Phase 2)**: Depends on Setup and blocks all
  helper extraction because modules import shared dataclasses and request types.
- **Shared Three-Phase Matcher (Phase 3)**: Depends on model ownership and is the
  MVP correctness gate for UID, overlap, same-start, trim, prefix, and mirror
  semantics.
- **Trim, Slot Bookkeeping, and Greedy Cleanup (Phase 4)**: Depends on models and
  matcher concepts; cleanup wiring depends on match/miss-count decision types.
- **Reconciliation Plan Application Helpers (Phase 5)**: Depends on models and
  can proceed after matching foundations because it preserves a separate side
  effect family.
- **Diagnostics Projection (Phase 6)**: Depends on apply/model state and finishes
  before final `async_apply_plan` validation.
- **Shell Delegation and Public Compatibility (Phase 7)**: Depends on matcher,
  trim, bookkeeping, cleanup, apply, and diagnostics helpers because it performs
  the sequential `event_overrides.py` shell work.
- **Parameter Reduction and Caller Compatibility (Phase 8)**: Depends on models,
  slot bookkeeping normalizers, and shell delegation; wrapper tests must land
  before the three 7-parameter signatures are removed.
- **Maintainability (Phase 9)**: Depends on all extraction, shell wiring, wrapper
  work, and shim cleanup. File-size measurement and `aislop` checks happen
  immediately before the complexity directive is removed.
- **Polish (Phase 10)**: Depends on all desired extraction and cleanup phases.

### User Story Dependencies

- **US1 (P1)**: Slot matching safety starts after Foundational and is the MVP
  safety gate; trim/prefix and greedy cleanup extend the same correctness area.
- **US2 (P1)**: Plan application can start after Foundational and must complete
  before final diagnostics and full refresh-cycle validation.
- **US3 (P1)**: Public compatibility depends on helper availability and completes
  after shell delegation and parameter-wrapper tests prove all caller forms.
- **US4 (P2)**: Maintainability follows US1-US3 because final file, function,
  parameter, shim, and directive-removal checks are meaningful only after the
  behavior-preserving split is complete.

### Within Each Story

- Focused tests are written before the corresponding helper extraction tasks and
  should fail or expose missing coverage until the extraction lands.
- `event_overrides_helpers/models.py` precedes every helper module that imports
  `OverrideSnapshot`, `TrimConfig`, matcher result types, request objects,
  eviction decisions, apply decisions, or diagnostics decisions.
- The matcher phase functions are shared by both mirror wrappers; do not create
  separate copied matchers for `_find_overlapping_slot` and
  `_slot_has_matching_event`.
- `_find_overlapping_slot` and `_slot_has_matching_event` are wired only after the
  shared matcher, trim helpers, and slot-bookkeeping helpers are stable.
- Clear/set/update/overwrite helpers return decisions only; `event_overrides.py`
  keeps Home Assistant reads, Keymaster service calls, locks, pending fences,
  pending-clear state, suppression markers, cache mutation, and diagnostics
  storage in the current order.
- `SlotUpdateRequest` and `SlotReservationRequest` wrapper tests must pin every
  accepted legacy call form before changing `async_reserve_or_get_slot`,
  `async_update`, or `update` signatures.
- Production caller import/usage verification happens after wrappers are wired
  and before maintainability cleanup.
- Temporary extraction shims are removed before final measurement.
- The complexity directive is removed only after immediate `wc -l` measurement
  proves each in-scope file is below 400 lines and `uv run pre-commit run aislop`
  passes; no replacement complexity suppression is allowed.

---

## Parallel Opportunities

- T012 and T013 can run in parallel after Phase 1 because matcher/model tests and
  apply decision tests touch different focused test files.
- T034, T035, and T036 can run in parallel after matcher/model contracts are
  stable because trim, slot bookkeeping, and greedy cleanup own different helper
  modules.
- T046, T047, T048, and T049 can be developed in parallel after T040-T045 because
  dispatch, clear, set, and update helpers own different modules.
- T053 and T054 can proceed after apply model decisions are stable and before
  final shell validation because diagnostics owns a separate helper module.
- T084, T085, and T086 can run independently once implementation is complete;
  T087 through T089 are final serial quality gates.

## Parallel Example: Helper Work After Models

```bash
Task: "Add matcher parity tests in tests/unit/test_event_overrides_matcher.py"
Task: "Add plan-application parity tests in tests/unit/test_event_overrides_apply.py"
Task: "Implement apply_clear helpers in custom_components/rental_control/event_overrides_helpers/apply_clear.py"
```

---

## Implementation Strategy

### MVP First (Slot Matching Safety)

1. Complete Phase 1 and Phase 2.
2. Add focused matcher parity tests for UID-positive exact-name, strict overlap,
   same-start bypass, trim/prefix fallback, eviction tolerance, and mirror
   consistency.
3. Implement the shared matcher and trim/bookkeeping helpers, then wire both
   mirror wrappers to the same phase implementation.
4. Stop and review matching parity before continuing to plan application and
   parameter reduction.

### Incremental Delivery

1. Build behavior-free models and the internal helper package beside the current
   `event_overrides.py` shell.
2. Extract the shared matcher, trim/prefix helpers, slot bookkeeping, and retired
   greedy cleanup decisions with focused tests.
3. Extract reconciliation plan-application decisions while keeping all async side
   effects and state mutation ordering in the shell.
4. Extract diagnostics projection and wire shell wrappers.
5. Introduce request-object normalizers and thin compatibility wrappers for the
   three 7-parameter methods, then verify all real caller forms.
6. Remove temporary shims, measure all in-scope files immediately before removing
   the complexity directive, and run `aislop` before and after removal.
7. Run targeted parity tests, new focused helper tests, full tests, ruff, staged
   `aislop`, and full pre-commit.

---

## Acceptance Coverage Map

| Coverage item | Primary tasks |
|---------------|---------------|
| US1 slot matching safety | T005, T008, T022-T039, T058, T061-T062, T084-T085 |
| US2 reconciliation plan application | T040-T057, T065, T086 |
| US3 compatibility surface | T006, T059-T065, T066-T074, T086 |
| US4 maintainability under aislop | T010-T014, T020, T075-T083, T089 |
| FR-001 observable behavior unchanged | T008-T009, T030, T039, T052, T057, T065, T074, T084-T087 |
| FR-002 existing tests unchanged | T007-T009, T030, T039, T052, T057, T065, T074, T084, T086-T087 |
| FR-003 three-phase order | T023-T028, T058, T061-T062, T085 |
| FR-004 UID-positive exact-name | T023, T027-T028, T058, T061-T062, T085 |
| FR-005 name plus strict overlap | T024, T027-T028, T058, T061-T062, T085 |
| FR-006 same-start UID bypass | T025-T028, T032, T035, T058, T061-T062, T085 |
| FR-007 trim/prefix fallback | T026, T031, T034, T058, T061-T062, T085 |
| FR-008 mirror relationship | T026-T028, T058, T061-T062, T085 |
| FR-009 eviction tolerance | T033, T036-T039, T084-T085 |
| FR-010 retired greedy shim | T033, T036-T039, T063, T084 |
| FR-011 plan dispatch | T040, T046, T050, T052, T086 |
| FR-012 clear application | T041, T047, T050, T052, T086 |
| FR-013 set and assign application | T042, T048, T050, T052, T086 |
| FR-014 update and overwrite application | T043-T044, T049-T050, T052, T086 |
| FR-015 reconciliation integration | T046-T052, T064, T086 |
| FR-016 coordinator integration | T006, T073-T074, T086 |
| FR-017 production compatibility surface | T006, T059-T065, T073-T074, T086 |
| FR-018 private regression seams | T005, T059, T063, T084 |
| FR-019 parameter-count strategy | T017, T066-T074, T078, T080, T082 |
| FR-020 complexity directive removal | T010, T079-T082, T089 |
| FR-021 file/function/parameter limits | T020, T077-T080, T082, T089 |
| FR-022 no new hot-path side effects | T011, T029, T038, T051, T056, T083, T092 |
| FR-023 behavior-preserving docs | T002-T004, T083, T090-T092 |
| SC-001 existing tests green | T008-T009, T084, T086-T087 |
| SC-002 matcher parity | T022-T030, T058, T061-T062, T085 |
| SC-003 cleanup parity | T033, T036-T039, T084-T085 |
| SC-004 plan-application parity | T040-T057, T086 |
| SC-005 caller compatibility | T006, T059-T065, T073-T074, T086 |
| SC-006 parameter threshold | T066-T074, T078, T080, T082 |
| SC-007 complexity thresholds | T075-T080, T082, T089 |
| SC-008 directive removed | T079-T082, T089 |
| SC-009 no added hot-path work | T011, T029, T038, T051, T056, T083, T092 |
| SC-010 docs-only tasks stage | This `tasks.md` PR only; implementation tasks start unchecked |
