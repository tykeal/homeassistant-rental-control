<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Tasks: Decompose Reconciliation Engine

**Input**: Design documents from `/specs/015-decompose-reconciliation/`
**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅,
quickstart.md ✅

**Tests**: Included and required. This feature is a behavior-preserving
refactor of the 3.6.0 stateless reconciliation engine, so existing
reconciliation tests remain the primary oracle and new focused phase tests
prove byte-for-byte parity for identity helpers, pairing, rematch, desired-plan
selection/classification, stateless-plan selection/classification, action
assembly, diagnostics, and public shims.

**Organization**: Tasks are grouped by setup, package/model foundations, the
ordered module split from PLAN, public compatibility, and final gates.
Implementation must convert `custom_components/rental_control/reconciliation.py`
into `custom_components/rental_control/reconciliation/` without changing any
production caller imports or existing test imports.

## Format: `- [ ] T### [P?] [Story?] Description with file path`

- **[P]**: Can run in parallel (different files, no dependency on incomplete
  tasks)
- **[Story]**: Which user story the task primarily proves (US1 through US4)
- Include exact file paths in descriptions
- Leave every checkbox unchecked until the implementation PR performs the task

## Path Conventions

- **Current monolith**: `custom_components/rental_control/reconciliation.py`
- **Target package**: `custom_components/rental_control/reconciliation/`
- **Production callers**: `custom_components/rental_control/coordinator.py`,
  `custom_components/rental_control/event_overrides.py`, and
  `custom_components/rental_control/sensors/calsensor.py`
- **Existing reconciliation tests**: `tests/unit/test_slot_reconciliation.py`,
  `tests/unit/test_event_overrides.py`, `tests/unit/test_coordinator.py`,
  `tests/unit/test_keymaster_event_diagnostics.py`,
  `tests/integration/test_refresh_cycle.py`, and
  `tests/integration/test_slot_concurrency.py`
- **New focused tests**: `tests/unit/test_reconciliation_imports.py`,
  `tests/unit/test_reconciliation_identity.py`,
  `tests/unit/test_reconciliation_pairing.py`,
  `tests/unit/test_reconciliation_rematch.py`,
  `tests/unit/test_reconciliation_desired_phases.py`,
  `tests/unit/test_reconciliation_stateless_phases.py`, and
  `tests/unit/test_reconciliation_actions_diagnostics.py`
- **Feature docs**: `specs/015-decompose-reconciliation/`

## Live Module Transition Scope

Implementation changes the live reconciliation engine only. The target module
split from PLAN is:

- `custom_components/rental_control/reconciliation/__init__.py` — compatibility
  boundary re-exporting the full consumed public surface.
- `custom_components/rental_control/reconciliation/enums.py` —
  `FINGERPRINT_VERSION`, `SlotStatus`, `ObservedSlotStatus`, and `ActionKind`.
- `custom_components/rental_control/reconciliation/action_models.py` —
  `SlotAction`.
- `custom_components/rental_control/reconciliation/plan_models.py` —
  `Reservation`, `ManagedSlot`, `PlannedSlot`, and `DesiredPlan`.
- `custom_components/rental_control/reconciliation/stateless_models.py` —
  `ObservedSlot`, `DesiredReservation`, and `StatelessPlan`.
- `custom_components/rental_control/reconciliation/store_models.py` —
  `CacheOnlyStoreRecord`, `StoredIdentity`, `StoredActual`, and `SlotMapping`.
- `custom_components/rental_control/reconciliation/rematch_models.py` —
  `RematchKind` and `RematchResult`.
- `custom_components/rental_control/reconciliation/identity.py` — fingerprint,
  UTC canonicalization, booking aliases, slot-name normalization, and name-form
  matching helpers.
- `custom_components/rental_control/reconciliation/pairing.py` — date-distance,
  subset selection, `_pair_partial_managed`, and `_pair_partial_observed`.
- `custom_components/rental_control/reconciliation/rematch_names.py` — rematch
  name-form and fresh-observed-name conflict helpers.
- `custom_components/rental_control/reconciliation/rematch_dates.py` — UTC date
  parsing and date-match helpers.
- `custom_components/rental_control/reconciliation/rematch_continuity.py` —
  conservative continuity and competition checks.
- `custom_components/rental_control/reconciliation/rematch.py` —
  `find_reservation_rematch` dispatcher and rule helpers.
- `custom_components/rental_control/reconciliation/desired.py` —
  `DesiredPlanRequest`, `compute_desired_plan` compatibility shim, and the ten
  named desired-plan phase helpers from PLAN.
- `custom_components/rental_control/reconciliation/stateless.py` —
  `StatelessPlanRequest`, `compute_stateless_plan`, and the stateless phase
  helpers from PLAN.
- `custom_components/rental_control/reconciliation/actions.py` — drift fields,
  `_build_slot_action`, action metadata, preflight flags, and confirmed-empty
  requirements.
- `custom_components/rental_control/reconciliation/diagnostics.py` — DesiredPlan
  and StatelessPlan diagnostic snapshots, redaction, ordering, and carry-over
  keys.

The public compatibility surface that must remain importable from
`custom_components.rental_control.reconciliation` is:
`ActionKind`, `CacheOnlyStoreRecord`, `DesiredPlan`, `DesiredReservation`,
`FINGERPRINT_VERSION`, `ManagedSlot`, `ObservedSlot`, `ObservedSlotStatus`,
`PlannedSlot`, `RematchKind`, `RematchResult`, `Reservation`, `SlotAction`,
`SlotMapping`, `SlotStatus`, `StatelessPlan`, `StoredActual`, `StoredIdentity`,
`compute_desired_plan`, `compute_stateless_plan`, `extract_booking_aliases`,
`find_reservation_rematch`, `make_reservation_fingerprint`, and
`normalize_slot_name_for_fingerprint`.

---

## Phase 1: Setup & Baseline (Shared Infrastructure)

**Purpose**: Establish the behavior, import, and complexity baseline before any
production code moves.

- [ ] T001 Run `.specify/scripts/bash/check-prerequisites.sh --json` with `SPECIFY_FEATURE=015-decompose-reconciliation` from the repository root and confirm `specs/015-decompose-reconciliation/` reports `research.md`, `data-model.md`, and `quickstart.md`
- [ ] T002 Inspect US1-US4, FR-001 through FR-019, constraints, and SC-001 through SC-010 in `specs/015-decompose-reconciliation/spec.md`
- [ ] T003 Inspect the Project Structure, Compatibility boundary, Module responsibilities, `compute_desired_plan` split, `compute_stateless_plan` split, and `aislop` directive removal sections in `specs/015-decompose-reconciliation/plan.md`
- [ ] T004 Inspect R-001 through R-009, the Compatibility Export Set, Engine Entities, request models, and quickstart validation scenarios in `specs/015-decompose-reconciliation/research.md`, `specs/015-decompose-reconciliation/data-model.md`, and `specs/015-decompose-reconciliation/quickstart.md`
- [ ] T005 Inventory `# aislop-ignore-file`, enums, dataclasses, identity helpers, `_pair_partial_managed`, `_pair_partial_observed`, `find_reservation_rematch`, `_build_slot_action`, `_build_plan_diagnostics_snapshot`, `compute_desired_plan`, and `compute_stateless_plan` in `custom_components/rental_control/reconciliation.py`
- [ ] T006 Inventory production import and call sites in `custom_components/rental_control/coordinator.py`, `custom_components/rental_control/event_overrides.py`, and `custom_components/rental_control/sensors/calsensor.py` and record that caller imports must not change
- [ ] T007 Inventory existing reconciliation behavior coverage in `tests/unit/test_slot_reconciliation.py`, `tests/unit/test_event_overrides.py`, `tests/unit/test_coordinator.py`, `tests/unit/test_keymaster_event_diagnostics.py`, `tests/integration/test_refresh_cycle.py`, and `tests/integration/test_slot_concurrency.py`
- [ ] T008 Run unchanged baseline reconciliation tests with `uv run pytest tests/unit/test_slot_reconciliation.py tests/unit/test_event_overrides.py tests/unit/test_coordinator.py tests/unit/test_keymaster_event_diagnostics.py tests/integration/test_refresh_cycle.py tests/integration/test_slot_concurrency.py -q` against the listed test files
- [ ] T009 Record the current line, function-length, and parameter-count baseline for `custom_components/rental_control/reconciliation.py`, including that `compute_desired_plan` has five positional inputs plus `entry_id`, `lockname`, and `start_slot` keywords

---

## Phase 2: Foundational Package and Models (Blocking Prerequisites)

**Purpose**: Create the package transition and move behavior-free constants,
enums, and dataclasses before modules that import them.

**⚠️ CRITICAL**: No identity, pairing, rematch, planner, action, or diagnostics
extraction can complete until model ownership is stable. Models must preserve
field names, defaults, enum values, `slots=True`, validation timing, and object
identity through the package root.

### Foundational Tests

- [ ] T010 [US3] Add compatibility import tests for every public root symbol and internal owner-module object identity in `tests/unit/test_reconciliation_imports.py`
- [ ] T011 [US4] Add model construction, enum value, dataclass default, validation, and `slots=True` parity tests for the target model modules in `tests/unit/test_reconciliation_imports.py`

### Foundational Implementation

- [ ] T012 Replace `custom_components/rental_control/reconciliation.py` with a temporary package shell at `custom_components/rental_control/reconciliation/__init__.py` that preserves current behavior and public imports while extraction begins
- [ ] T013 [P] [US3] Create `custom_components/rental_control/reconciliation/enums.py` with `FINGERPRINT_VERSION`, `SlotStatus`, `ObservedSlotStatus`, and `ActionKind` copied unchanged from the temporary package shell
- [ ] T014 [P] [US3] Create `custom_components/rental_control/reconciliation/action_models.py` with `SlotAction` copied unchanged from the temporary package shell
- [ ] T015 [P] [US3] Create `custom_components/rental_control/reconciliation/plan_models.py` with `Reservation`, `ManagedSlot`, `PlannedSlot`, and `DesiredPlan` copied unchanged from the temporary package shell
- [ ] T016 [P] [US3] Create `custom_components/rental_control/reconciliation/stateless_models.py` with `ObservedSlot`, `DesiredReservation`, and `StatelessPlan` copied unchanged from the temporary package shell
- [ ] T017 [P] [US3] Create `custom_components/rental_control/reconciliation/store_models.py` with `CacheOnlyStoreRecord`, `StoredIdentity`, `StoredActual`, and `SlotMapping` copied unchanged from the temporary package shell
- [ ] T018 [P] [US3] Create `custom_components/rental_control/reconciliation/rematch_models.py` with `RematchKind` and `RematchResult` copied unchanged from the temporary package shell
- [ ] T019 [US3] Wire temporary imports in `custom_components/rental_control/reconciliation/__init__.py` so existing root imports resolve to the exact objects owned by `enums.py`, `action_models.py`, `plan_models.py`, `stateless_models.py`, `store_models.py`, and `rematch_models.py`
- [ ] T020 [US4] Verify all model modules in `custom_components/rental_control/reconciliation/` stay side-effect-free and do not import planner modules, Home Assistant APIs, Store APIs, or coordinator code
- [ ] T021 Run foundational validation with `uv run pytest tests/unit/test_reconciliation_imports.py tests/unit/test_slot_reconciliation.py -q` against the listed test files

**Checkpoint**: The reconciliation import path is now a package, behavior-free
models are owned by focused modules, and FR-014/FR-015 public names still import
from the root.

---

## Phase 3: Identity, Fingerprints, and Name Forms (Priority: P1)

**Goal**: Preserve fingerprint, alias, and slot-name identity behavior exactly.

**Independent Test**: Compare normalized names, v1 fingerprint strings, UTC
canonicalization, Airbnb alias extraction, prefix stripping, exact display-name
matching, and no-generic-prefix rejection for identical inputs.

### Tests for Identity

- [ ] T022 [US1] Add fingerprint, UTC canonicalization, booking alias, redaction-boundary, and legacy fixture parity tests in `tests/unit/test_reconciliation_identity.py`
- [ ] T023 [US1] Add trim-aware, prefix-aware, exact display-name, normalized form, and unsafe generic-prefix rejection tests in `tests/unit/test_reconciliation_identity.py`

### Implementation for Identity

- [ ] T024 [US1] Extract `normalize_slot_name_for_fingerprint`, `make_reservation_fingerprint`, `extract_booking_aliases`, `_dt_to_utc_iso`, `_desired_name_forms`, `_slot_name_variants`, `_names_match`, `_reservation_name_key`, and `_desired_name_key` unchanged into `custom_components/rental_control/reconciliation/identity.py`
- [ ] T025 [US1] Update `custom_components/rental_control/reconciliation/__init__.py` and internal modules to import identity helpers from `custom_components/rental_control/reconciliation/identity.py` without changing root import behavior
- [ ] T026 [US1] Verify `custom_components/rental_control/coordinator.py` and `custom_components/rental_control/sensors/calsensor.py` continue importing `make_reservation_fingerprint` and `normalize_slot_name_for_fingerprint` from the root with no caller import edits
- [ ] T027 Run identity validation with `uv run pytest tests/unit/test_reconciliation_identity.py tests/unit/test_slot_reconciliation.py tests/unit/test_coordinator.py -q` against the listed test files

**Checkpoint**: Identity helpers prove FR-005, FR-011, FR-014, SC-004, and the
slot-name identity part of SC-002 without introducing new matching rules.

---

## Phase 4: Pairing and Reservation Rematch (Priority: P1)

**Goal**: Preserve duplicate-name pairing, minimum-distance physical matching,
and the Store rematch hierarchy without giving Store data authority.

**Independent Test**: Exercise duplicate names, duplicate physical slot-name
matches, shifted dates, exact/alias/name-time/continuity rematches, ambiguity,
fresh-observed-name conflicts, and cache-only Store scenarios with identical
results.

### Tests for Pairing and Rematch

- [ ] T028 [US1] Add managed and observed pairing tests for start/end ordering, minimum-distance subsets, deterministic slot fallback, partial duplicate pairing, and one selected reservation per slot in `tests/unit/test_reconciliation_pairing.py`
- [ ] T029 [US1] Add no-duplicate-assignment regression tests for reservation length changes, date shifts, same-guest rebookings, duplicate guest names, and duplicate physical slot-name matches in `tests/unit/test_reconciliation_pairing.py`
- [ ] T030 [US2] Add rematch hierarchy tests for exact fingerprint, fresh physical-name conflict, UID alias plus name with `date_shifted=True`, booking alias plus name, name plus exact UTC time, continuity, ambiguity, and no-match in `tests/unit/test_reconciliation_rematch.py`
- [ ] T031 [US2] Add cache-only Store tests for missing, deleted, stale, contradictory, and mid-run deleted mapping data in `tests/unit/test_reconciliation_rematch.py`

### Implementation for Pairing and Rematch

- [ ] T032 [US1] Extract `_slot_times_match`, `_datetime_distance`, `_managed_slot_distance`, `_observed_slot_distance`, `_select_managed_subset`, `_select_observed_subset`, `_pair_partial_managed`, and `_pair_partial_observed` unchanged into `custom_components/rental_control/reconciliation/pairing.py`
- [ ] T033 [P] [US2] Create `custom_components/rental_control/reconciliation/rematch_names.py` with `_get_nested`, `_normalized_name_forms`, `_mapping_name_forms`, `_mapping_name_matches_reservation`, `_is_adopted_mapping`, `_should_include_observed_mapping`, and `_fresh_observed_name_conflicts` copied unchanged from the temporary package shell
- [ ] T034 [P] [US2] Create `custom_components/rental_control/reconciliation/rematch_dates.py` with `_as_utc_datetime` and `_mapping_dates_match_reservation` copied unchanged from the temporary package shell
- [ ] T035 [P] [US2] Create `custom_components/rental_control/reconciliation/rematch_continuity.py` with `_is_continuity_compatible` and `_has_competing_reservation` copied unchanged from the temporary package shell
- [ ] T036 [US2] Extract `find_reservation_rematch` into `custom_components/rental_control/reconciliation/rematch.py` as a dispatcher with exact, UID-alias, booking-alias, name-time, continuity, ambiguity, and no-match rule helpers, preserving the current rule order and continuity date tie-break
- [ ] T037 [US1] Update desired/stateless temporary code paths to call `custom_components/rental_control/reconciliation/pairing.py` helpers for duplicate matching without duplicating pairing logic
- [ ] T038 [US2] Update rematch imports in `custom_components/rental_control/reconciliation/__init__.py` so `find_reservation_rematch`, `RematchKind`, and `RematchResult` remain root-importable
- [ ] T039 Run pairing/rematch validation with `uv run pytest tests/unit/test_reconciliation_pairing.py tests/unit/test_reconciliation_rematch.py tests/unit/test_slot_reconciliation.py -q` against the listed test files

**Checkpoint**: Pairing and rematch prove FR-001, FR-005, FR-006, FR-009,
FR-010, FR-014, FR-015, SC-002, SC-004, and SC-006.

---

## Phase 5: DesiredPlan Phase Decomposition (Priority: P1) 🎯 MVP

**Goal**: Split the legacy `compute_desired_plan` body into the exact ten phase
helpers from PLAN while preserving selected reservations, overflow, slot
matching, actions, diagnostics, and no-op suppression.

**Independent Test**: Run existing desired-plan tests unchanged and focused
phase tests that compare selected/protected/overflow, stable-name matching,
assignment, classification, action order, and diagnostics to the current output.

### Tests for DesiredPlan Phases

- [ ] T040 [US2] Add deterministic plan serialization helpers for dataclasses, enums, datetimes, sorted sets, actions, and diagnostics in `tests/unit/test_reconciliation_desired_phases.py`
- [ ] T041 [US2] Add desired-plan selection tests for `eligible`, `checked_out`, `missing_count`, protected-active first selection, non-protected `(start, identity_key)` ordering, capacity overflow ranks, and overflow reasons in `tests/unit/test_reconciliation_desired_phases.py`
- [ ] T042 [US1] Add desired-plan stable-name matching tests for persisted identity, exact-time matches, partial-pair minimum distance, duplicate physical-slot canonical selection, and changed-reservation in-place updates in `tests/unit/test_reconciliation_desired_phases.py`
- [ ] T043 [US1] Add desired-plan classification tests for pending clear, blocked, unknown, duplicate, stale, phantom, mis-assigned, drift, `UPDATE_TIMES`, `NOOP`, and confirmed-reset-before-reapply metadata in `tests/unit/test_reconciliation_desired_phases.py`

### Implementation for DesiredPlan Phases

- [ ] T044 [US2] Add `DesiredPlanRequest` and `build_desired_plan_request()` in `custom_components/rental_control/reconciliation/desired.py` with validation for legacy arguments and `entry_id`, `lockname`, and `start_slot` context
- [ ] T045 [US2] Implement `select_eligible_reservations()`, `select_desired_candidates()`, and `record_capacity_overflow()` in `custom_components/rental_control/reconciliation/desired.py`, preserving protected-first selection, soonest ordering, selected ranks, and `overflow_details`
- [ ] T046 [US1] Implement `group_selected_by_stable_name()` and `match_existing_managed_slots()` in `custom_components/rental_control/reconciliation/desired.py`, preserving stable-name grouping, persisted-identity hints, exact-time matches, partial-pairing, and duplicate slot tracking
- [ ] T047 [US1] Implement `assign_unmatched_reservations()` in `custom_components/rental_control/reconciliation/desired.py`, preserving lowest confirmed-free slot allocation, protected-slot behavior, and `no_empty_slot` overflow
- [ ] T048 [US1] Implement `classify_desired_plan_slots()` in `custom_components/rental_control/reconciliation/desired.py`, preserving stale, phantom, duplicate, pending-clear, blocked, drift, update-in-place, and no-op classifications
- [ ] T049 [US2] Implement `assemble_desired_actions()` in `custom_components/rental_control/reconciliation/desired.py`, preserving action ordering, `NOOP` suppression from `DesiredPlan.actions`, and confirmed-empty/preflight metadata
- [ ] T050 [US2] Implement `build_desired_diagnostics()` in `custom_components/rental_control/reconciliation/desired.py`, preserving keys, sorting, redaction, context fields, drift fields, retry counts, and carry-over behavior
- [ ] T051 [US2] Refactor the desired-plan entry path in `custom_components/rental_control/reconciliation/desired.py` so all ten helpers are called in PLAN order and every helper is below 80 lines with no more than six project-owned parameters
- [ ] T052 Run desired-plan validation with `uv run pytest tests/unit/test_reconciliation_desired_phases.py tests/unit/test_slot_reconciliation.py tests/unit/test_coordinator.py tests/integration/test_refresh_cycle.py -q` against the listed test files

**Checkpoint**: DesiredPlan decomposition proves FR-001, FR-002, FR-003,
FR-005, FR-006, FR-007, FR-008, FR-012, FR-013, FR-018, SC-001, SC-002,
SC-003, SC-004, and SC-005 for the legacy planner.

---

## Phase 6: StatelessPlan Phase Decomposition (Priority: P1)

**Goal**: Split `compute_stateless_plan` into the phase helpers from PLAN while
preserving observed-slot classification, selected assignments, overflow,
actions, action order, matched-slot fields, and diagnostics.

**Independent Test**: Run existing stateless tests unchanged and focused phase
tests for selected ranks, prefix-aware matching, duplicate observed slots,
confirmed-empty assignment, update-in-place replacement, date updates, and
diagnostics parity.

### Tests for StatelessPlan Phases

- [ ] T053 [US2] Add stateless selection tests for observed-slot classification, eligible/protected/non-protected ordering, selected ranks, and capacity overflow in `tests/unit/test_reconciliation_stateless_phases.py`
- [ ] T054 [US1] Add stateless matching tests for prefix-aware stable/display forms, exact-time matches, partial observed pairing, duplicate canonical selection, and `matched_slot`/`assigned_slot` mutation in `tests/unit/test_reconciliation_stateless_phases.py`
- [ ] T055 [US1] Add stateless action tests for unreadable blocks, duplicate resets, stale resets, assignments, update-in-place replacement, date-only `UPDATE_TIMES`, confirmed-empty requirements, and preflight reads in `tests/unit/test_reconciliation_stateless_phases.py`

### Implementation for StatelessPlan Phases

- [ ] T056 [US2] Add `StatelessPlanRequest`, `build_stateless_plan_request()`, and `initialize_stateless_plan()` in `custom_components/rental_control/reconciliation/stateless.py` without changing the six-parameter `compute_stateless_plan` caller contract
- [ ] T057 [US2] Implement `select_stateless_reservations()` in `custom_components/rental_control/reconciliation/stateless.py`, preserving eligible filtering, protected ordering, selected ranks, and capacity overflow
- [ ] T058 [US1] Implement `group_stateless_reservations_by_name()` and `match_observed_slots_by_name()` in `custom_components/rental_control/reconciliation/stateless.py`, preserving prefix-aware matching, exact-time matches, partial observed pairing, duplicate slot tracking, and mutation semantics
- [ ] T059 [US1] Implement `assign_unmatched_stateless_reservations()` in `custom_components/rental_control/reconciliation/stateless.py`, preserving lowest confirmed-empty assignment and `no_empty_slot` overflow
- [ ] T060 [US1] Implement `build_stateless_actions()` in `custom_components/rental_control/reconciliation/stateless.py`, preserving unreadable blocked actions, duplicate resets, stale resets, assignments, update-in-place replacement, date updates, action ordering, and confirmed-reset-before-reapply safety
- [ ] T061 [US2] Implement `build_stateless_diagnostics()` in `custom_components/rental_control/reconciliation/stateless.py`, preserving the current selected, overflow, observed-slot, action, and generated-at snapshot shape
- [ ] T062 [US2] Refactor `compute_stateless_plan()` in `custom_components/rental_control/reconciliation/stateless.py` to call the stateless helpers in PLAN order while keeping the public six-parameter signature and keeping each helper below 80 lines
- [ ] T063 Run stateless validation with `uv run pytest tests/unit/test_reconciliation_stateless_phases.py tests/unit/test_slot_reconciliation.py tests/integration/test_refresh_cycle.py -q` against the listed test files

**Checkpoint**: StatelessPlan decomposition proves FR-001, FR-002, FR-004,
FR-005, FR-006, FR-007, FR-008, FR-012, FR-013, FR-018, SC-001, SC-002,
SC-003, SC-004, and SC-005 for the stateless planner.

---

## Phase 7: Actions and Diagnostics Extraction (Priority: P1)

**Goal**: Centralize action construction and diagnostics snapshots after both
planner flows are phase-split, preserving action metadata and redacted output
byte-for-byte.

**Independent Test**: Compare action objects, action order, reasons, matched-by
labels, blocked reasons, retry counts, preflight flags, confirmed-empty flags,
drift fields, and diagnostics dictionaries for identical desired/stateless
inputs.

### Tests for Actions and Diagnostics

- [ ] T064 [US1] Add action assembly tests for `NOOP`, `ASSIGN`, `UPDATE_IN_PLACE`, `RESET`, `SET`, `UPDATE_TIMES`, `CLEAR`, `RETRY_CLEAR`, `OVERWRITE_MANUAL_CHANGE`, `BLOCKED`, reasons, matched-by labels, blocked reasons, retry counts, and last errors in `tests/unit/test_reconciliation_actions_diagnostics.py`
- [ ] T065 [US1] Add confirmed-reset-before-reapply tests proving replacements, duplicates, stale occupants, phantoms, and drift corrections retain `requires_confirmed_empty` and `preflight_read` semantics in `tests/unit/test_reconciliation_actions_diagnostics.py`
- [ ] T066 [US2] Add diagnostics equivalence tests for DesiredPlan and StatelessPlan metadata, slot details, reservation details, selected/overflow data, action summaries, alias sorting, drift fields, context fields, carry-over keys, and raw-code redaction in `tests/unit/test_reconciliation_actions_diagnostics.py`

### Implementation for Actions and Diagnostics

- [ ] T067 [US1] Extract `_compute_drift_fields`, `_build_slot_action`, and shared action metadata helpers unchanged into `custom_components/rental_control/reconciliation/actions.py`
- [ ] T068 [US1] Wire `custom_components/rental_control/reconciliation/desired.py` and `custom_components/rental_control/reconciliation/stateless.py` to use `custom_components/rental_control/reconciliation/actions.py` without changing action order or action object fields
- [ ] T069 [US2] Extract `_build_plan_diagnostics_snapshot` and stateless diagnostics builders into `custom_components/rental_control/reconciliation/diagnostics.py`, split into plan metadata, slot diagnostics, reservation diagnostics, action diagnostics, and carry-over helpers
- [ ] T070 [US2] Wire `custom_components/rental_control/reconciliation/desired.py` and `custom_components/rental_control/reconciliation/stateless.py` to use `custom_components/rental_control/reconciliation/diagnostics.py` while preserving diagnostics byte-for-byte for identical inputs
- [ ] T071 [US4] Verify `custom_components/rental_control/reconciliation/actions.py` and `custom_components/rental_control/reconciliation/diagnostics.py` contain no Home Assistant I/O, Store authority, coordinator refreshes, Keymaster service calls, or state writes
- [ ] T072 Run actions/diagnostics validation with `uv run pytest tests/unit/test_reconciliation_actions_diagnostics.py tests/unit/test_reconciliation_desired_phases.py tests/unit/test_reconciliation_stateless_phases.py tests/unit/test_slot_reconciliation.py -q` against the listed test files

**Checkpoint**: Actions and diagnostics prove FR-008, FR-012, FR-013, FR-018,
SC-003, SC-005, and raw-code redaction safety.

---

## Phase 8: Public Compatibility, Caller Verification, and Cleanup (Priority: P1)

**Goal**: Finalize the package root and public shims so all production and test
callers use the same import names and call patterns with no caller rewrites.

**Independent Test**: Import every compatibility symbol from the package root,
compare it to its owner-module object, exercise legacy and request-object
planner calls, and prove production import sites need no changes.

### Tests for Public Compatibility

- [ ] T073 [US3] Finalize public root import tests for the full Compatibility Export Set in `tests/unit/test_reconciliation_imports.py`
- [ ] T074 [US3] Add `compute_desired_plan` shim tests for five positional legacy arguments plus `entry_id`, `lockname`, and `start_slot` keywords, direct `DesiredPlanRequest` calls, missing required values, and unknown context keyword rejection in `tests/unit/test_reconciliation_imports.py`
- [ ] T075 [US3] Add production caller import tests that import `custom_components.rental_control.coordinator`, `custom_components.rental_control.event_overrides`, and `custom_components.rental_control.sensors.calsensor` without changing their reconciliation import lines in `tests/unit/test_reconciliation_imports.py`

### Implementation for Public Compatibility

- [ ] T076 [US3] Finalize `compute_desired_plan()` in `custom_components/rental_control/reconciliation/desired.py` as a no-more-than-six-parameter compatibility shim with five explicit legacy parameters plus validated `**context`, delegating to `DesiredPlanRequest` and the ten phase helpers
- [ ] T077 [US3] Finalize `compute_stateless_plan()` in `custom_components/rental_control/reconciliation/stateless.py` as the unchanged six-parameter compatibility entry point delegating to `StatelessPlanRequest` and phase helpers
- [ ] T078 [US3] Rewrite `custom_components/rental_control/reconciliation/__init__.py` as a thin compatibility boundary that re-exports only the planned public surface from owner modules and includes no temporary monolith logic
- [ ] T079 [US3] Verify no import edits are required in `custom_components/rental_control/coordinator.py`, `custom_components/rental_control/event_overrides.py`, `custom_components/rental_control/sensors/calsensor.py`, or existing reconciliation tests; any changes in those files must be limited to behavior-preserving test additions or removed before review
- [ ] T080 [US4] Remove temporary extraction shims from `custom_components/rental_control/reconciliation/__init__.py` and `custom_components/rental_control/reconciliation/*.py`, leaving only the permanent public root re-exports and planned request-object compatibility shims
- [ ] T081 [US4] Confirm `custom_components/rental_control/reconciliation.py` no longer exists as a module file and all live reconciliation code resides under `custom_components/rental_control/reconciliation/`
- [ ] T082 [US4] Remove the `# aislop-ignore-file complexity/file-too-large complexity/function-too-long` directive from the reconciliation engine only after `custom_components/rental_control/reconciliation/*.py` files are below 400 lines, functions are below 80 lines, and project-owned parameter lists are no more than six parameters
- [ ] T083 Run public-compatibility validation with `uv run pytest tests/unit/test_reconciliation_imports.py tests/unit/test_slot_reconciliation.py tests/unit/test_event_overrides.py tests/unit/test_coordinator.py tests/unit/test_keymaster_event_diagnostics.py -q` against the listed test files

**Checkpoint**: Public compatibility proves FR-002, FR-014, FR-015, FR-016,
FR-017, SC-001, SC-007, SC-008, and SC-009.

---

## Phase 9: Polish & Cross-Cutting Acceptance Gates

**Purpose**: Verify behavior parity, safety invariants, quality gates,
traceability, and no unintended production behavior changes.

### Acceptance and Quality Gates

- [ ] T084 Run unchanged existing reconciliation parity tests with `uv run pytest tests/unit/test_slot_reconciliation.py tests/unit/test_event_overrides.py tests/unit/test_coordinator.py tests/unit/test_keymaster_event_diagnostics.py tests/integration/test_refresh_cycle.py tests/integration/test_slot_concurrency.py -q` against the listed test files
- [ ] T085 Run all new focused phase tests with `uv run pytest tests/unit/test_reconciliation_imports.py tests/unit/test_reconciliation_identity.py tests/unit/test_reconciliation_pairing.py tests/unit/test_reconciliation_rematch.py tests/unit/test_reconciliation_desired_phases.py tests/unit/test_reconciliation_stateless_phases.py tests/unit/test_reconciliation_actions_diagnostics.py -q` against the listed test files
- [ ] T086 Verify no selected reservation appears in more than one managed or observed slot across the length-change, date-shift, code-change, same-guest rebooking, duplicate-name, and duplicate-physical-slot cases in `tests/unit/test_reconciliation_pairing.py`, `tests/unit/test_reconciliation_desired_phases.py`, `tests/unit/test_reconciliation_stateless_phases.py`, and `tests/integration/test_refresh_cycle.py`
- [ ] T087 Verify stable slot-name identity, in-place reservation updates, confirmed-reset-before-reapply ordering, and cache-only Store semantics have explicit tests in `tests/unit/test_reconciliation_identity.py`, `tests/unit/test_reconciliation_pairing.py`, `tests/unit/test_reconciliation_rematch.py`, and `tests/unit/test_reconciliation_actions_diagnostics.py`
- [ ] T088 Verify `compute_desired_plan` and `compute_stateless_plan` produce byte-for-byte equivalent serialized plan outputs, actions, action ordering, and diagnostics for identical inputs in `tests/unit/test_reconciliation_desired_phases.py`, `tests/unit/test_reconciliation_stateless_phases.py`, and `tests/unit/test_reconciliation_actions_diagnostics.py`
- [ ] T089 Verify every FR-001 through FR-019 and every SC-001 through SC-010 has a test, implementation, or acceptance task mapped in `specs/015-decompose-reconciliation/tasks.md`
- [ ] T090 Confirm no new lock-code business rules, reconciliation states, automations, configuration options, Store authority, Home Assistant coordinator refreshes, blocking I/O, Keymaster service calls, or unrelated Rental Control public APIs were introduced in `custom_components/rental_control/reconciliation/*.py`
- [ ] T091 Stage the implementation files and run the staged aislop hook with `uv run pre-commit run aislop`; confirm every `custom_components/rental_control/reconciliation/*.py` file is below 400 lines, all in-scope functions are below 80 lines, project-owned parameter lists have no more than six parameters, and no new `aislop` suppression exists
- [ ] T092 Run full regression tests with `uv run pytest tests/ -x -q` against `tests/`
- [ ] T093 Run linting with `uv run ruff check custom_components/ tests/` against `custom_components/` and `tests/`
- [ ] T094 Run full pre-commit validation with `uv run pre-commit run --all-files` against repository-tracked files, including reuse, yamllint, actionlint, aislop, ruff, ruff-format, mypy, interrogate, and gitlint hooks
- [ ] T095 Review `specs/015-decompose-reconciliation/quickstart.md` and confirm the implementation PR notes list unchanged existing parity commands, new focused phase test commands, no-duplicate-assignment safeguards, public import verification, `aislop` directive removal, and final validation results

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup & Baseline (Phase 1)**: No dependencies.
- **Foundational Package and Models (Phase 2)**: Depends on Setup and blocks all
  other extraction work.
- **Identity (Phase 3)**: Depends on model ownership; planner and rematch phases
  depend on these helpers.
- **Pairing and Rematch (Phase 4)**: Depends on models and identity; desired and
  stateless planners depend on pairing, while Store rematch remains independently
  testable.
- **DesiredPlan (Phase 5)**: Depends on models, identity, and pairing.
- **StatelessPlan (Phase 6)**: Depends on models, identity, and pairing; may
  proceed after pairing even if desired-plan extraction is in review, but final
  gates require both planners.
- **Actions and Diagnostics (Phase 7)**: Depends on desired and stateless phase
  boundaries so shared action/diagnostic helpers can be wired without changing
  planner outputs.
- **Public Compatibility (Phase 8)**: Depends on all owner modules; caller-import
  verification and shim cleanup happen before `aislop` directive removal.
- **Polish (Phase 9)**: Depends on all extraction phases and final public
  compatibility.

### User Story Dependencies

- **US1 (P1)**: Safety checks start with identity/pairing and continue through
  desired/stateless/action gates; this is the MVP safety gate.
- **US2 (P1)**: Plan-output parity depends on desired/stateless phases and final
  diagnostics equivalence.
- **US3 (P1)**: Public surface compatibility starts with model exports and
  finishes after root `__init__.py` and shim finalization.
- **US4 (P2)**: Maintainability follows extraction because file/function/parameter
  thresholds and shim removal are meaningful only after behavior is decomposed.

### Within Each Story

- Focused tests are written before the corresponding extraction tasks and should
  fail or expose missing coverage until extraction lands.
- `enums.py`, `action_models.py`, `plan_models.py`, `stateless_models.py`,
  `store_models.py`, and `rematch_models.py` precede every module that imports
  them.
- `identity.py` precedes `pairing.py`, rematch helpers, desired phases, and
  stateless phases.
- `pairing.py` precedes desired/stateless stable-name matching and duplicate
  disambiguation phases.
- Rematch helper modules precede the `find_reservation_rematch` dispatcher.
- `DesiredPlanRequest` and `StatelessPlanRequest` precede public compatibility
  shim finalization.
- Root `__init__.py` re-export verification happens after owner modules exist and
  before caller-import verification.
- Temporary compatibility shims are removed before the `aislop` directive is
  removed; `aislop` removal is gated by passing thresholds, not by preference.

---

## Parallel Opportunities

- T013 through T018 can run in parallel after T012 because each owns a different
  model module.
- T028 and T030 can run in parallel because pairing and rematch tests touch
  different files.
- T033 through T035 can run in parallel after identity extraction because rematch
  name, date, and continuity helpers own different files.
- T084 and T085 can run independently once implementation is complete; T091
  through T094 are final serial quality gates.

## Parallel Example: Pairing and Rematch After Identity

```bash
Task: "Add pairing tests in tests/unit/test_reconciliation_pairing.py"
Task: "Add rematch hierarchy tests in tests/unit/test_reconciliation_rematch.py"
Task: "Extract rematch date helpers in custom_components/rental_control/reconciliation/rematch_dates.py"
```

---

## Implementation Strategy

### MVP First (Safety and DesiredPlan)

1. Complete Phase 1 and Phase 2.
2. Complete identity, pairing, and rematch extraction with focused tests.
3. Complete DesiredPlan phase extraction and validate no-duplicate-assignment,
   stable-name matching, in-place updates, confirmed-reset-before-reapply, and
   diagnostics parity.
4. Stop and review behavior parity before continuing to StatelessPlan and shared
   action/diagnostics extraction.

### Incremental Delivery

1. Convert the monolith path to a temporary package shell without changing root
   imports.
2. Move behavior-free model definitions into owner modules.
3. Extract identity, pairing, and rematch helpers under focused parity tests.
4. Split desired and stateless planners by PLAN phase names without changing
   outputs.
5. Extract action and diagnostics helpers after both planners have stable phase
   boundaries.
6. Finalize root re-exports and public shims, prove callers need no import
   changes, remove temporary shims, then remove the `aislop` directive.
7. Run targeted parity tests, new focused phase tests, full tests, ruff, staged
   `aislop`, and full pre-commit.

---

## Acceptance Coverage Map

| Coverage item | Primary tasks |
|---------------|---------------|
| US1 duplicate-assignment safety | T028-T029, T032, T037, T042-T049, T054-T060, T064-T065, T086-T088 |
| US2 desired/stateless plan parity | T040-T052, T053-T063, T066-T070, T084-T085, T088 |
| US3 public surface compatibility | T010, T019, T025-T026, T038, T073-T079, T083 |
| US4 maintainability under aislop | T011, T020, T051, T062, T071, T080-T082, T091 |
| FR-001 no duplicate assignment | T028-T029, T042-T049, T054-T060, T086 |
| FR-002 existing tests unchanged | T007-T008, T021, T027, T039, T052, T063, T072, T083-T084 |
| FR-003 DesiredPlan equivalence | T040-T052, T088 |
| FR-004 StatelessPlan equivalence | T053-T063, T088 |
| FR-005 slot-name identity | T022-T027, T028-T032, T042, T054, T087 |
| FR-006 duplicate disambiguation | T028-T032, T042-T046, T054-T058, T086 |
| FR-007 in-place updates | T029, T042, T046-T048, T054-T060, T087 |
| FR-008 confirmed reset before reapply | T043, T048-T049, T055, T060, T064-T065, T087 |
| FR-009 cache-only Store | T031, T033-T036, T087, T090 |
| FR-010 rematch hierarchy | T030, T033-T036, T039 |
| FR-011 fingerprint and aliases | T022-T027 |
| FR-012 diagnostics parity | T050, T061, T066, T069-T070, T088 |
| FR-013 action building | T043, T049, T055, T060, T064-T068, T087 |
| FR-014 production public surface | T006, T010, T025-T026, T073-T079, T083 |
| FR-015 test-consumed public surface | T010-T019, T038, T073-T078, T083 |
| FR-016 remove aislop directive | T082, T091 |
| FR-017 file/function/parameter limits | T009, T051, T062, T076-T077, T080-T082, T091 |
| FR-018 no hot-path side effects | T020, T071, T090 |
| FR-019 behavior-preserving docs | T002-T004, T089-T090, T095 |
| SC-001 existing tests green | T008, T084, T092 |
| SC-002 selected once | T028-T029, T042-T049, T054-T060, T086 |
| SC-003 byte-for-byte plans | T040-T052, T053-T063, T066-T070, T088 |
| SC-004 stable identity matching | T022-T027, T028-T032, T042, T054, T087 |
| SC-005 confirmed-empty replacement | T043, T049, T055, T060, T064-T065, T087 |
| SC-006 Store cache-only | T031, T033-T036, T087, T090 |
| SC-007 caller compatibility | T073-T079, T083 |
| SC-008 complexity thresholds | T051, T062, T071, T080-T082, T091 |
| SC-009 aislop directive removed | T082, T091 |
| SC-010 docs-only pipeline stage | This `tasks.md` PR only; implementation tasks start unchecked |
