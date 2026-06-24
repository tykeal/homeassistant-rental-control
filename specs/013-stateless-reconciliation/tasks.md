<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Tasks: Stateless Slot Reconciliation

**Input**: Design documents from `/specs/013-stateless-reconciliation/`
**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅,
quickstart.md ✅

**Tests**: Included and required. This feature changes safety-critical
physical-access lock-code reconciliation, so every user story, functional
requirement, success criterion, and must-pass duplicate-avoidance scenario has
explicit unit or integration test tasks before implementation tasks.

**Organization**: Tasks are grouped by user story after shared setup and
foundational work. The redesign replaces persisted-authoritative slot mappings,
Store fences, ghost reservations, adoption machinery, and per-event greedy
allocation with one stateless per-refresh planner. `event_N` sensors remain
read-only reflections of the latest calendar data and stateless plan.

## Format: `- [X] T### [P?] [Story?] Description with file path`

- **[P]**: Can run in parallel (different files, no dependency on incomplete
  tasks)
- **[Story]**: Which user story the task belongs to (US1 through US6)
- Include exact file paths in descriptions

## Path Conventions

- **Source**: `custom_components/rental_control/` at repository root
- **Sensors**: `custom_components/rental_control/sensors/`
- **Tests**: `tests/unit/`, `tests/integration/`, and `tests/fixtures/`
- **Feature docs**: `specs/013-stateless-reconciliation/`

## Live Module Transition Scope

Implementation changes or retires the live modules called out by PLAN and
research: `custom_components/rental_control/coordinator.py`,
`custom_components/rental_control/event_overrides.py`,
`custom_components/rental_control/reconciliation.py`,
`custom_components/rental_control/util.py`,
`custom_components/rental_control/sensor.py`,
`custom_components/rental_control/sensors/calsensor.py`,
`custom_components/rental_control/sensors/checkinsensor.py`,
`custom_components/rental_control/__init__.py`, and
`custom_components/rental_control/const.py`.

The real existing test files to update include
`tests/unit/test_slot_reconciliation.py`, `tests/unit/test_event_overrides.py`,
`tests/unit/test_coordinator.py`, `tests/unit/test_util.py`,
`tests/unit/test_sensors.py`, `tests/unit/test_checkin_sensor.py`,
`tests/integration/test_refresh_cycle.py`,
`tests/integration/test_slot_concurrency.py`, and
`tests/integration/test_checkin_tracking.py`. Many existing tests assert the
old persisted-authoritative behavior and must be migrated, removed, or replaced
when the obsolete machinery is retired.

---

## Phase 1: Setup & Baseline (Shared Infrastructure)

**Purpose**: Establish artifact, code, and existing-test baselines before
changing reconciliation behavior.

- [X] T001 Inspect spec.md user stories US1-US6, FR-001 through FR-021, and SC-001 through SC-011 in specs/013-stateless-reconciliation/spec.md
- [X] T002 Inspect R-001 through R-008 decisions and retired machinery notes in specs/013-stateless-reconciliation/research.md
- [X] T003 Inspect ObservedSlot, DesiredReservation, SlotAction, StatelessPlan, CacheOnlyStoreRecord, matching rules, and state transitions in specs/013-stateless-reconciliation/data-model.md
- [X] T004 Inspect validation scenarios and expected commands in specs/013-stateless-reconciliation/quickstart.md
- [X] T005 Inventory current persisted-authoritative and greedy allocation paths in custom_components/rental_control/coordinator.py, custom_components/rental_control/event_overrides.py, and custom_components/rental_control/reconciliation.py
- [X] T006 Inventory current sensor, check-in, Store setup, and Keymaster helper dependencies in custom_components/rental_control/sensors/calsensor.py, custom_components/rental_control/sensors/checkinsensor.py, custom_components/rental_control/__init__.py, custom_components/rental_control/util.py, and custom_components/rental_control/const.py
- [X] T007 Inventory obsolete persisted-authoritative tests to migrate or remove in tests/unit/test_event_overrides.py, tests/unit/test_slot_reconciliation.py, tests/unit/test_coordinator.py, tests/integration/test_refresh_cycle.py, and tests/integration/test_slot_concurrency.py
- [X] T008 Run baseline targeted tests for tests/unit/test_slot_reconciliation.py, tests/unit/test_event_overrides.py, tests/unit/test_coordinator.py, tests/unit/test_util.py, tests/unit/test_sensors.py, tests/unit/test_checkin_sensor.py, tests/integration/test_refresh_cycle.py, and tests/integration/test_slot_concurrency.py

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Build the new stateless model alongside the old runtime path: typed
physical/desirable data structures, pure planner entry point, stable-name
identity matching, cache-only Store load/migration, and shared fixtures.

**⚠️ CRITICAL**: No user story switchover work can begin until this phase is
complete.

### Foundational Tests

- [X] T009 [P] Add ObservedSlot, DesiredReservation, SlotAction, StatelessPlan, CacheOnlyStoreRecord validation tests in tests/unit/test_slot_reconciliation.py
- [X] T010 [P] Add physical empty, occupied, phantom, unknown, unreadable, and unmanaged ObservedSlot classification tests in tests/unit/test_slot_reconciliation.py
- [X] T011 [P] Add stable slot-name normalization tests for exact, case-insensitive, prefix-stripped, trimmed, and prefixed-trimmed names in tests/unit/test_slot_reconciliation.py
- [X] T012 Add duplicate-name start-time pairing, trim-collision blocking, and one-slot-per-desired invariant tests in tests/unit/test_slot_reconciliation.py
- [X] T013 Add pure planner invariant tests proving Store/cache fields do not affect selected reservations, actions, overflow, or blocked slots in tests/unit/test_slot_reconciliation.py
- [X] T014 [P] Add cache-only Store v1 migration tests for ignored legacy status, slot, operation_id, pending_clear_since, blocked_slots, and missing_count fields in tests/unit/test_coordinator.py
- [X] T015 Add cache load failure, corrupt cache, duplicate cache claims, no-raw-PIN, and best-effort save tests in tests/unit/test_coordinator.py
- [X] T016 [P] Add shared stateless fixtures for observed slots, desired reservations, legacy cache records, Keymaster snapshots, and operation results in tests/fixtures/event_data.py
- [X] T017 Run foundational failing-test check for tests/unit/test_slot_reconciliation.py, tests/unit/test_coordinator.py, and tests/fixtures/event_data.py

### Foundational Implementation

- [X] T018 Create ObservedSlotStatus, ObservedSlot, DesiredReservation, SlotAction, StatelessPlan, and CacheOnlyStoreRecord types in custom_components/rental_control/reconciliation.py
- [X] T019 Implement stable-name normalization, prefix removal, trim-aware matching, duplicate-name start-order grouping, and ambiguity diagnostics in custom_components/rental_control/reconciliation.py
- [X] T020 Implement the stateless pure planner entry point that accepts only ObservedSlot records, DesiredReservation records, and immutable configuration in custom_components/rental_control/reconciliation.py
- [X] T021 Implement cache-only Store load and legacy migration helpers that return aliases and diagnostics without statuses or fences in custom_components/rental_control/coordinator.py
- [X] T022 Implement cache-only Store constants and diagnostic field names without authoritative status constants in custom_components/rental_control/const.py
- [X] T023 Add stateless observed-slot fixture builders and legacy-cache builders used by planner and coordinator tests in tests/fixtures/event_data.py
- [X] T024 Run foundational validation for tests/unit/test_slot_reconciliation.py, tests/unit/test_coordinator.py, and tests/fixtures/event_data.py

**Checkpoint**: The stateless planner model, stable-name matcher, cache-only Store
loader, and fixtures exist beside the current runtime path without switching
coordinator behavior.

---

## Phase 3: User Story 1 - Prevent Duplicates Across Reservation Changes (Priority: P1) 🎯 MVP

**Goal**: Changed reservations update their existing physical slot by stable
slot-name identity and never receive a duplicate managed slot.

**Independent Test**: Seed physical Keymaster slots with existing reservations,
change date length, full date range, code, or duplicate-name ordering, run the
stateless planner or refresh, and verify each selected reservation appears in at
most one managed slot.

### Tests for User Story 1

> **NOTE: Write these tests FIRST and ensure they fail until US1 behavior is
> implemented.**

- [X] T025 [P] [US1] Add planner tests for reservation length increase updating the same physical slot with no duplicate in tests/unit/test_slot_reconciliation.py
- [X] T026 [P] [US1] Add planner tests for reservation length decrease updating the same physical slot with no duplicate in tests/unit/test_slot_reconciliation.py
- [X] T027 [US1] Add planner tests for full date shift with date_based code change emitting same-slot replace_code and no second assignment in tests/unit/test_slot_reconciliation.py
- [X] T028 [US1] Add planner tests for same-guest rebooking and back-to-back stays using trim-aware name plus start-time order without duplicate slots in tests/unit/test_slot_reconciliation.py
- [X] T029 [US1] Add planner tests for duplicate guest names across two concurrent reservations disambiguated by start time with each reservation in exactly one slot in tests/unit/test_slot_reconciliation.py
- [X] T030 [US1] Add planner tests for simultaneous date shift plus duplicate guest names with deterministic pairing and ambiguous reorder blocking diagnostics in tests/unit/test_slot_reconciliation.py
- [X] T031 [US1] Add no-op tests proving already-correct physical name, PIN, and dates perform no Keymaster action in tests/unit/test_slot_reconciliation.py
- [X] T032 [US1] Add integration scenario for length increase then length decrease preserving one physical slot in tests/integration/test_refresh_cycle.py
- [X] T033 [US1] Add integration scenario for full date shift replacing old date_based code in the same slot and no duplicate slot in tests/integration/test_refresh_cycle.py
- [X] T034 [US1] Add integration scenario for duplicate names plus date shift and back-to-back rebooking with each stay in exactly one slot in tests/integration/test_refresh_cycle.py

### Implementation for User Story 1

- [X] T035 [US1] Implement in-place planner actions for same stable slot-name identity with date drift, display-name drift, or generated-code drift in custom_components/rental_control/reconciliation.py
- [X] T036 [US1] Implement duplicate-name start-time disambiguation and selected-reservation uniqueness enforcement in custom_components/rental_control/reconciliation.py
- [X] T037 [US1] Add diagnostics for stable-name matches, in-place updates, duplicate-name pairing, ambiguous groups, and duplicate-prevention blocks in custom_components/rental_control/reconciliation.py
- [X] T038 [US1] Run US1 validation for tests/unit/test_slot_reconciliation.py and tests/integration/test_refresh_cycle.py

**Checkpoint**: US1 proves SC-001, SC-002, SC-003, FR-004, FR-005, FR-006,
FR-007, FR-013, and the headline duplicate-avoidance scenarios.

---

## Phase 4: User Story 2 - Reconcile From Physical Truth Every Refresh (Priority: P1)

**Goal**: Every refresh derives correctness from current physical Keymaster state
and the current calendar; Store data is cache-only and cannot wedge behavior.

**Independent Test**: Run identical physical/calendar scenarios with Store
present, deleted, stale, contradictory, corrupt, or saved mid-run and verify the
same physical actions and resulting managed slots.

### Tests for User Story 2

- [X] T039 [P] [US2] Add coordinator tests proving _observe_managed_slots ignores Store status, pending clear, blocked slot, and missing count fields when classifying physical slots in tests/unit/test_coordinator.py
- [X] T040 [US2] Add store-non-authoritative matrix tests for cache present, missing, stale, contradictory, corrupt, and deleted mid-run producing identical plans in tests/unit/test_coordinator.py
- [X] T041 [US2] Add cold start, deleted Store, and first-upgrade tests proving existing coded slots are recognized by physical name and reconciled in place without wipe or adoption in tests/unit/test_coordinator.py
- [X] T042 [US2] Add contradictory Store tests where physical Bob beats cached Alice and no manual Store deletion is required in tests/unit/test_coordinator.py
- [X] T043 [US2] Add stale pending-clear cache tests proving a physically empty slot is assignable and no persisted fence remains load-bearing in tests/unit/test_coordinator.py
- [X] T044 [US2] Add unreadable Keymaster entity tests proving unavailable slots block for one cycle and re-evaluate normally on later refresh in tests/unit/test_coordinator.py
- [X] T045 [US2] Add no-calendar-reservation reset-and-free tests proving stale physical occupants reset only through confirmed empty state in tests/unit/test_slot_reconciliation.py
- [X] T046 [US2] Add integration scenario for deleted Store cold start recognizing existing coded slots by name without wiping them in tests/integration/test_refresh_cycle.py
- [X] T047 [US2] Add integration scenario for deleting or corrupting the Store mid-run and verifying identical refresh behavior in tests/integration/test_refresh_cycle.py

### Implementation for User Story 2

- [X] T048 [US2] Switch coordinator refresh to build ObservedSlot records from physical Keymaster entities without persisted status or fence inputs in custom_components/rental_control/coordinator.py
- [X] T049 [US2] Switch coordinator reservation building to DesiredReservation records from current calendar, current check-in state, and observed physical override inputs without ghost reservations in custom_components/rental_control/coordinator.py
- [X] T050 [US2] Replace refresh-level Store loading as runtime authority with cache-only alias and diagnostics inputs in custom_components/rental_control/coordinator.py
- [X] T051 [US2] Remove first-upgrade adoption as a correctness path from async_setup_entry while keeping a safe optional one-shot readability refresh in custom_components/rental_control/__init__.py
- [X] T052 [US2] Publish latest StatelessPlan and desired-reservation lookup accessors for sensors and check-in consumers in custom_components/rental_control/coordinator.py
- [X] T053 [US2] Write cache-only aliases and redacted last-plan diagnostics after refresh without raw PINs or authoritative statuses in custom_components/rental_control/coordinator.py
- [X] T054 [US2] Run US2 validation for tests/unit/test_coordinator.py, tests/unit/test_slot_reconciliation.py, and tests/integration/test_refresh_cycle.py

**Checkpoint**: US2 proves SC-005, FR-001, FR-002, FR-018 cold-start recovery,
and the STORE-IS-NON-AUTHORITATIVE acceptance gate.

---

## Phase 5: User Story 3 - Program the Soonest Eligible Reservations (Priority: P1)

**Goal**: Managed slots hold the soonest eligible reservations up to capacity,
while protected active guests count against capacity and are not evicted.

**Independent Test**: Feed more eligible reservations than managed slots,
including farther physical occupants and active guests, and verify selected slots
equal the protected-active plus soonest-N set after confirmed operations.

### Tests for User Story 3

- [X] T055 [P] [US3] Add pure soonest-N overflow tests selecting earliest starts up to managed capacity in tests/unit/test_slot_reconciliation.py
- [X] T056 [US3] Add active-guest protection tests proving checked-in guests remain selected first and count against slot capacity in tests/unit/test_slot_reconciliation.py
- [X] T057 [US3] Add reservation-dropout tests proving a reservation leaving soonest-N resets and frees its slot before a newly eligible reservation is programmed in tests/unit/test_slot_reconciliation.py
- [X] T058 [US3] Add farther-full-nearer-arrives tests proving the nearer reservation enters and farthest unprotected reservation overflows after confirmed reset in tests/unit/test_slot_reconciliation.py
- [X] T059 [US3] Add overflow diagnostics tests for capacity, no_empty_slot, unreadable_slot, and active_protected reasons in tests/unit/test_slot_reconciliation.py
- [X] T060 [US3] Add integration scenario for soonest-N overflow plus active-guest protection in tests/integration/test_refresh_cycle.py
- [X] T061 [US3] Add integration scenario for reservation dropout from soonest-N followed by confirmed reset and newly eligible programming in tests/integration/test_refresh_cycle.py

### Implementation for User Story 3

- [X] T062 [US3] Implement protected-active selection before non-protected soonest-N capacity fill in custom_components/rental_control/reconciliation.py
- [X] T063 [US3] Implement deterministic overflow, dropout, farthest-unprotected reset, and newly eligible assignment planning in custom_components/rental_control/reconciliation.py
- [X] T064 [US3] Wire active check-in state and checked-out state into DesiredReservation construction in custom_components/rental_control/coordinator.py
- [X] T065 [US3] Preserve check-in tracking active-reservation surfaces while reading latest plan ownership for protection in custom_components/rental_control/sensors/checkinsensor.py
- [X] T066 [US3] Run US3 validation for tests/unit/test_slot_reconciliation.py, tests/unit/test_coordinator.py, tests/unit/test_checkin_sensor.py, tests/integration/test_refresh_cycle.py, and tests/integration/test_checkin_tracking.py

**Checkpoint**: US3 proves SC-004, SC-009, FR-003, FR-008, FR-016, and the
soonest-N overflow/dropout acceptance gates.

---

## Phase 6: User Story 4 - Reuse Slots Only After Confirmed Reset (Priority: P1)

**Goal**: A slot receives a new or replacement PIN only after an immediate
physical read confirms both name and PIN are empty; stale Store fences never
control reuse.

**Independent Test**: Force reset confirmation to lag or fail, run refreshes, and
verify no replacement set happens until physical empty state is confirmed by
fresh Keymaster reads.

### Tests for User Story 4

- [X] T067 [P] [US4] Add util tests proving async_fire_clear_code reports confirmed only when physical name and PIN are cleared in tests/unit/test_util.py
- [X] T068 [P] [US4] Add util tests proving lingering name, lingering PIN, service failure, and unavailable text state leave clear unconfirmed in tests/unit/test_util.py
- [X] T069 [P] [US4] Add util tests proving async_fire_set_code and async_fire_update_times use bounded confirmation and return unconfirmed on timeout in tests/unit/test_util.py
- [X] T070 [US4] Add apply-path tests proving update_in_place(replace_code) fresh-reads, clears, confirms empty, then sets the same desired reservation into the same slot in tests/unit/test_event_overrides.py
- [X] T071 [US4] Add apply-path tests proving unconfirmed clear skips replacement set and the next planner run retries from physical state without Store fences in tests/unit/test_event_overrides.py
- [X] T072 [US4] Add physical-empty tests for blank, unknown, UNKNOWN, None, and case-insensitive text states, plus unavailable-not-free in tests/unit/test_slot_reconciliation.py
- [X] T073 [US4] Add integration scenario for confirmed-reset-before-reapply with lagging clear then later empty confirmation in tests/integration/test_refresh_cycle.py
- [X] T074 [US4] Add callback re-entrancy tests proving callbacks never launch nested reconciliation and compatibility updates use request_refresh=False in tests/integration/test_slot_concurrency.py

### Implementation for User Story 4

- [X] T075 [US4] Implement fresh preflight reads and confirmed-empty gates before clear, assign, and replacement set operations in custom_components/rental_control/event_overrides.py
- [X] T076 [US4] Preserve bounded confirmation OperationResult semantics for clear, set, and update_times helpers in custom_components/rental_control/util.py
- [X] T077 [US4] Implement apply ordering for clear/update before assign and same-slot replace_code without cross-slot double-programming in custom_components/rental_control/event_overrides.py
- [X] T078 [US4] Remove persisted pending-clear fences from assignment safety and rely on observed physical occupied/empty/unknown state in custom_components/rental_control/event_overrides.py
- [X] T079 [US4] Keep callback suppression and single-lock reconciliation apply behavior without nested refreshes in custom_components/rental_control/util.py and custom_components/rental_control/event_overrides.py
- [X] T080 [US4] Run US4 validation for tests/unit/test_util.py, tests/unit/test_event_overrides.py, tests/unit/test_slot_reconciliation.py, tests/integration/test_refresh_cycle.py, and tests/integration/test_slot_concurrency.py

**Checkpoint**: US4 proves SC-006, SC-007 safety behavior, FR-009, FR-010,
FR-011, FR-012, and confirmed-reset-before-reapply.

---

## Phase 7: User Story 5 - Preserve Existing Guest Access Semantics (Priority: P1)

**Goal**: The redesign changes reconciliation authority only; manual overrides,
buffers, Honor PMS times, deterministic code behavior, check-in tracking, and
read-only `event_N` sensor behavior remain observable-compatible.

**Independent Test**: Exercise preserved user-visible semantics before and after
stateless reconciliation and verify unchanged access windows, codes, sensors,
tracking, and display attributes.

### Tests for User Story 5

- [X] T081 [P] [US5] Add manual check-in and checkout time-of-day override regression tests with Honor Event Times off in tests/unit/test_coordinator.py
- [X] T082 [P] [US5] Add Honor Event Times regression tests for timed events, all-day events, description fallback, and configured defaults in tests/unit/test_coordinator.py
- [X] T083 [P] [US5] Add manual door-code override tests proving observed manual PINs are preserved in memory and never persisted or logged in tests/unit/test_slot_reconciliation.py
- [X] T084 [P] [US5] Add buffer regression tests proving lock-code before and after buffers produce existing Keymaster date ranges in tests/unit/test_util.py
- [X] T085 [P] [US5] Add slot-name trimming, prefixing, deterministic code generation, and should_update_code regression tests in tests/unit/test_util.py and tests/unit/test_slot_reconciliation.py
- [X] T086 [US5] Add event_N sensor tests proving slot_number and slot_code read from latest StatelessPlan, including date-shift fingerprint-to-desired-id bridge, without calling async_reserve_or_get_slot in tests/unit/test_sensors.py
- [X] T087 [US5] Add check-in tracking tests proving sensor attributes, active protection, checked-out release, and unlock validation use latest plan ownership rather than override maps in tests/unit/test_checkin_sensor.py
- [X] T088 [US5] Add integration scenario preserving manual time-of-day overrides, manual door-code overrides, buffers, Honor PMS times, check-in tracking, and event_N sensors in tests/integration/test_refresh_cycle.py
- [X] T089 [US5] Add integration scenario preserving check-in tracking through reconciliation changes in tests/integration/test_checkin_tracking.py
- [X] T132 [US5] Add buffer-aware manual-override regression tests proving
  Honor Event Times follows calendar check-in/check-out changes after applying
  before/after buffers, buffer=0 still follows calendar time changes, and true
  manual deviations from the buffered expected time remain preserved in
  tests/unit/test_coordinator.py and tests/unit/test_slot_reconciliation.py

### Implementation for User Story 5

- [X] T090 [US5] Derive manual time-of-day override input from matched physical slot dates when Honor Event Times does not override them in custom_components/rental_control/coordinator.py
- [X] T091 [US5] Preserve manual observed PINs as in-memory desired codes while keeping raw PINs out of Store and logs in custom_components/rental_control/reconciliation.py and custom_components/rental_control/coordinator.py
- [X] T092 [US5] Preserve existing buffer, Honor Event Times, deterministic code generation, should_update_code, prefix, and trimming semantics in custom_components/rental_control/coordinator.py and custom_components/rental_control/util.py
- [X] T093 [US5] Make event_N sensors read slot_number and slot_code from latest StatelessPlan compatibility lookup without reserving slots in custom_components/rental_control/sensors/calsensor.py
- [X] T094 [US5] Make check-in unlock validation read latest stateless plan and observed slot ownership rather than deleted EventOverrides maps in custom_components/rental_control/sensors/checkinsensor.py
- [X] T095 [US5] Keep sensor platform setup and entity naming unchanged in custom_components/rental_control/sensor.py
- [X] T096 [US5] Run US5 validation for tests/unit/test_coordinator.py, tests/unit/test_util.py, tests/unit/test_sensors.py, tests/unit/test_checkin_sensor.py, tests/integration/test_refresh_cycle.py, and tests/integration/test_checkin_tracking.py

**Checkpoint**: US5 proves SC-008, SC-010, FR-014, FR-015, FR-017, FR-022, and
preserved sensor/check-in semantics.

---

## Phase 8: User Story 6 - Self-Heal Physical Empty and Drifted Slots (Priority: P2)

**Goal**: Normal refreshes correct stale, phantom, duplicate, manually drifted,
physically empty, and unmanaged-slot states without restarts, reloads, manual
clear-all, or manual persisted-store deletion.

**Independent Test**: Seed drifted physical slot states, run normal refreshes,
and verify convergence to the should-be physical state when Keymaster becomes
readable and required operations are confirmed.

### Tests for User Story 6

- [X] T097 [P] [US6] Add physical-empty self-heal tests for empty, blank, unknown, UNKNOWN, and None name/PIN states regardless of stale cache in tests/unit/test_slot_reconciliation.py
- [X] T098 [P] [US6] Add stale occupant, phantom name-only, duplicate physical occupant, and manually drifted name/PIN/date planner tests in tests/unit/test_slot_reconciliation.py
- [X] T099 [P] [US6] Add unmanaged-slot ignored tests proving slots outside the Rental-Control-managed range never receive actions in tests/unit/test_slot_reconciliation.py
- [X] T100 [P] [US6] Add caplog and diagnostics tests for reset, blocked, stable-name update, manual drift correction, duplicate collapse, and outside-should-be skip without raw PINs in tests/unit/test_event_overrides.py
- [X] T101 [US6] Add integration scenario for physical-empty self-heal from stale cache and assigning a selected reservation in tests/integration/test_refresh_cycle.py
- [X] T102 [US6] Add integration scenario for stale, phantom, duplicate, and manually drifted slots converging without restart, reload, clear-all, or Store deletion in tests/integration/test_refresh_cycle.py
- [X] T103 [US6] Add integration scenario proving unmanaged Keymaster slots are never modified during stale or duplicate recovery in tests/integration/test_refresh_cycle.py

### Implementation for User Story 6

- [X] T104 [US6] Implement stale, phantom, duplicate, manual drift, physical-empty, unknown, and unmanaged-slot action planning in custom_components/rental_control/reconciliation.py
- [X] T105 [US6] Apply stale, phantom, duplicate, and manual drift corrections through the single apply lock and confirmed-clear safety path in custom_components/rental_control/event_overrides.py
- [X] T106 [US6] Add redacted logs and diagnostics for reset, blocked, stable-name update, drift correction, duplicate collapse, and outside-should-be skip in custom_components/rental_control/reconciliation.py and custom_components/rental_control/event_overrides.py
- [X] T107 [US6] Run US6 validation for tests/unit/test_slot_reconciliation.py, tests/unit/test_event_overrides.py, and tests/integration/test_refresh_cycle.py

**Checkpoint**: US6 proves SC-011, FR-018, FR-019, FR-020, and P2 self-healing
supportability behavior.

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: Retire obsolete machinery and obsolete tests, verify acceptance
coverage and repository quality gates, and document final validation.

### Obsolete Machinery Retirement

- [X] T108 Remove _next_slot, next_slot, __assign_next_slot, and greedy slot selection from custom_components/rental_control/event_overrides.py
- [X] T109 Remove _slot_uids and UID-authoritative matching phases from _find_overlapping_slot in custom_components/rental_control/event_overrides.py
- [X] T110 Remove _slot_miss_counts and Store ghost reservation correctness logic from custom_components/rental_control/event_overrides.py and custom_components/rental_control/coordinator.py
- [X] T111 Remove _pending_clear_slots, _pending_fences, persisted operation IDs, and persisted blocked slots as assignment fences from custom_components/rental_control/event_overrides.py and custom_components/rental_control/coordinator.py
- [X] T112 Remove load_persisted_mappings validation as runtime authority and replace it with cache-only alias/diagnostic loading in custom_components/rental_control/event_overrides.py
- [X] T113 Remove async_reserve_or_get_slot, _find_overlapping_slot, async_check_overrides, and production callers from custom_components/rental_control/event_overrides.py and custom_components/rental_control/sensors/calsensor.py
- [X] T114 Remove async_adopt_keymaster_slots, _adopt_observed_coded_slots, Store rematch/re-key helpers, find_reservation_rematch production use, and ghost reservation construction from custom_components/rental_control/coordinator.py and custom_components/rental_control/reconciliation.py
- [X] T115 Remove obsolete Store status, pending operation, blocked slot, miss count, and adopted-slot constants that are no longer needed from custom_components/rental_control/const.py

### Obsolete Test Migration

- [X] T116 Migrate or remove tests that assert next_slot, async_reserve_or_get_slot, _find_overlapping_slot phases, UID-authoritative matching, and greedy overflow in tests/unit/test_event_overrides.py
- [X] T117 Migrate or remove tests that assert persisted fingerprints, find_reservation_rematch, Store ghost reservations, miss counts, pending fences, and adoption-on-observe as correctness inputs in tests/unit/test_slot_reconciliation.py
- [X] T118 Migrate or remove tests that assert async_adopt_keymaster_slots, _adopt_observed_coded_slots, Store re-keying, persisted status classification, and pending-clear import in tests/unit/test_coordinator.py
- [X] T119 Migrate or remove integration tests that assert missing Store adoption, persisted UID churn, two-cycle slot-mapping miss tolerance, and reappearing-before-third-miss Store behavior in tests/integration/test_refresh_cycle.py
- [X] T120 Migrate or remove concurrency tests that assert legacy reserve-or-get-slot locking instead of stateless apply locking in tests/integration/test_slot_concurrency.py
- [X] T121 Run migrated obsolete-test validation for tests/unit/test_event_overrides.py, tests/unit/test_slot_reconciliation.py, tests/unit/test_coordinator.py, tests/integration/test_refresh_cycle.py, and tests/integration/test_slot_concurrency.py

### Acceptance and Quality Gates

- [X] T122 Verify every FR-001 through FR-022 has a unit or integration acceptance test task mapped in specs/013-stateless-reconciliation/tasks.md
- [X] T123 Verify every SC-001 through SC-011 has a unit or integration acceptance test task mapped in specs/013-stateless-reconciliation/tasks.md
- [X] T124 Verify duplicate-avoidance must-pass scenarios cover length increase, length decrease, full date shift with code change, same-guest back-to-back rebooking, duplicate guest names, and duplicate names plus date shift in tests/unit/test_slot_reconciliation.py and tests/integration/test_refresh_cycle.py
- [X] T125 Verify store-non-authoritative must-pass scenarios cover missing, deleted, stale, contradictory, corrupt, mid-run deletion, and save failure cases in tests/unit/test_coordinator.py and tests/integration/test_refresh_cycle.py
- [X] T126 Run targeted acceptance tests from quickstart.md for tests/unit/test_slot_reconciliation.py, tests/unit/test_event_overrides.py, tests/unit/test_util.py, tests/unit/test_coordinator.py, tests/unit/test_sensors.py, tests/unit/test_checkin_sensor.py, tests/integration/test_refresh_cycle.py, tests/integration/test_slot_concurrency.py, and tests/integration/test_checkin_tracking.py
- [X] T127 Run full test suite with uv run pytest tests/ -x -q
- [X] T128 Run linting with uv run ruff check custom_components/ tests/
- [X] T129 Run type and documentation quality gates with uv run pre-commit run mypy --all-files and uv run pre-commit run interrogate --all-files
- [X] T130 Run all pre-commit hooks with uv run pre-commit run --all-files
- [X] T131 Validate quickstart manual verification notes and final expected commands in specs/013-stateless-reconciliation/quickstart.md

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup & Baseline (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup and blocks all user stories.
- **US1 Duplicate Avoidance (Phase 3)**: Depends on Foundational; MVP scope.
- **US2 Physical Truth (Phase 4)**: Depends on Foundational and benefits from
  US1 stable-name matching before coordinator switchover.
- **US3 Soonest-N (Phase 5)**: Depends on Foundational and coordinator
  switchover from US2.
- **US4 Confirmed Reset (Phase 6)**: Depends on US1-US3 action semantics and
  apply-path wiring.
- **US5 Preserved Semantics (Phase 7)**: Depends on US2 latest-plan accessors and
  US4 apply semantics.
- **US6 Self-Heal (Phase 8)**: Depends on US1-US4 planner/apply safety.
- **Polish (Phase 9)**: Depends on all user-story phases. Obsolete machinery is
  removed only after the new planner, coordinator, sensors, apply path, and tests
  are in place.

### User Story Dependencies

- **US1 (P1)**: Can start after Foundational; provides duplicate-prevention MVP.
- **US2 (P1)**: Should follow US1 to reuse stable-name matching for physical truth.
- **US3 (P1)**: Should follow US2 coordinator switchover for soonest-N runtime
  behavior.
- **US4 (P1)**: Should follow US3 action selection so apply ordering protects all
  replacement and dropout paths.
- **US5 (P1)**: Should follow US2 latest-plan accessors and US4 confirmed apply
  behavior.
- **US6 (P2)**: Should follow US1-US4 so drift correction uses the same safety
  gates and matching rules.

### Within Each User Story

- Tests MUST be written first and initially fail for new behavior.
- Pure planner tests precede coordinator integration.
- Coordinator switchover precedes sensor read-only plan lookups.
- Apply-path confirmed reset tests precede EventOverrides/util changes.
- Obsolete tests are migrated or removed only after equivalent stateless tests are
  passing.

---

## Parallel Opportunities

- Setup inspection tasks T001-T007 can be split across agents; T008 runs after
  inventories are complete.
- Foundational test tasks T009-T016 are mostly independent by file before T017.
- US1 planner test tasks T025-T031 can be developed in parallel with integration
  test scaffolding T032-T034 after shared fixtures exist.
- US3, US4, US5, and US6 unit tests marked [P] can be assigned by file while a
  separate developer works on integration scenarios in `tests/integration/`.
- Polish migration tasks T116-T120 can run by test file after retirement tasks
  T108-T115 identify the removed APIs.

## Parallel Example: User Story 1

```bash
Task: "Add planner tests for reservation length increase in tests/unit/test_slot_reconciliation.py"
Task: "Add planner tests for reservation length decrease in tests/unit/test_slot_reconciliation.py"
Task: "Add integration scenario for full date shift in tests/integration/test_refresh_cycle.py"
```

## Parallel Example: User Story 5

```bash
Task: "Add manual override tests in tests/unit/test_coordinator.py"
Task: "Add buffer regression tests in tests/unit/test_util.py"
Task: "Add event_N sensor latest-plan tests in tests/unit/test_sensors.py"
Task: "Add check-in latest-plan ownership tests in tests/unit/test_checkin_sensor.py"
```

---

## Implementation Strategy

### MVP First (US1 Only)

1. Complete Phase 1 and Phase 2.
2. Complete Phase 3 tests and implementation for stable-name in-place updates.
3. Validate US1 independently with unit planner tests plus targeted integration
   duplicate-avoidance scenarios.
4. Stop and review before coordinator-wide persisted-authority removal.

### Incremental Delivery

1. Build stateless data structures, matcher, planner, and cache-only Store load
   alongside the current runtime path.
2. Prove US1 duplicate-avoidance in the pure planner.
3. Switch the coordinator refresh cycle to observed physical truth and
   DesiredReservation inputs.
4. Add soonest-N, confirmed reset, preserved semantics, and self-heal behavior.
5. Make `event_N` and check-in sensors read from the latest stateless plan.
6. Retire obsolete machinery and migrate/remove obsolete tests.
7. Run targeted acceptance, full suite, ruff, mypy, interrogate, and pre-commit.

---

## Acceptance Coverage Map

| Coverage item | Primary test tasks |
|---------------|--------------------|
| FR-001 physical + calendar are authoritative | T039, T040, T048, T054 |
| FR-002 Store cache-only | T013, T014, T015, T040, T047, T125 |
| FR-003 soonest eligible set | T055, T058, T062, T066 |
| FR-004 stable name before code/date | T011, T019, T025, T027, T035 |
| FR-005 changed reservation same slot | T025, T026, T027, T032, T033 |
| FR-006 prefix and trimming | T011, T085, T092 |
| FR-007 duplicate names by start order | T012, T029, T030, T036 |
| FR-008 reset outside should-be unless active | T045, T057, T063 |
| FR-009 assign only confirmed-empty slots | T057, T070, T075 |
| FR-010 no non-empty replacement PIN | T070, T071, T073, T077 |
| FR-011 empty means blank/unknown/None | T010, T072, T097 |
| FR-012 unavailable conservative | T044, T068, T072 |
| FR-013 already correct no-op | T031 |
| FR-014 manual time overrides | T081, T088, T090 |
| FR-015 manual door code overrides | T083, T088, T091 |
| FR-016 active guest protection | T056, T060, T064 |
| FR-017 preserved trimming/buffers/Honor PMS/sensors | T082, T084, T085, T086, T087, T092, T093, T094 |
| FR-018 self-heal without manual recovery | T097, T098, T101, T102, T104 |
| FR-019 managed scope only | T099, T103 |
| FR-020 diagnostics visibility | T037, T059, T100, T106 |
| FR-021 acceptance coverage set | T124, T125, T126 |
| SC-001 length/date/rebooking no duplicates | T025, T026, T027, T028, T032, T033 |
| SC-002 date_based old code replaced same slot | T027, T033 |
| SC-003 duplicate names exactly one slot | T029, T030, T034 |
| SC-004 programmed slots equal should-be | T055, T060, T061 |
| SC-005 missing/deleted/stale Store neutral | T040, T041, T046, T047 |
| SC-006 confirmed reset before reuse | T070, T071, T073 |
| SC-007 physical-empty self-heal | T072, T097, T101 |
| SC-008 manual overrides preserved | T081, T083, T088 |
| SC-009 active guest not evicted | T056, T060 |
| SC-010 existing semantics unchanged | T082, T084, T085, T086, T087, T088 |
| SC-011 drifted states converge | T098, T101, T102, T103 |

---

## Atomic Commit Sequence

1. `Feat(reconciliation): Add stateless models` — data structures and fixtures.
2. `Feat(reconciliation): Add stateless planner` — pure planner plus
   stable-name identity matching.
3. `Feat(store): Demote slot store to cache` — cache-only load/migration and
   Store-neutral tests.
4. `Feat(coordinator): Use stateless slot plan` — coordinator switchover from
   persisted-authoritative inputs to physical truth and calendar.
5. `Feat(slots): Confirm reset before apply` — fresh physical reads and safe
   apply ordering.
6. `Feat(sensors): Read slots from plan` — `event_N` and check-in latest-plan
   read-only accessors.
7. `Refactor(slots): Retire old reconciliation` — remove obsolete machinery.
8. `Test(slots): Migrate stateless coverage` — migrate/remove obsolete tests and
   ensure all acceptance tests pass.
9. `Chore(slots): Polish validation gates` — full suite, ruff, mypy,
   interrogate, pre-commit, and quickstart validation.
10. `Docs(tasks): Mark spec 013 complete` — separate tasks.md-completion commit
    only after implementation tasks are actually completed.

---

## Notes

- `[P]` tasks use different files or independent test areas and have no dependency
  on incomplete tasks in the same phase.
- All task checkboxes intentionally remain unchecked for the implementation PR.
- Do not implement runtime code in the TASKS-stage PR that adds this file.
- Do not close GitHub issue #607 from the tasks-stage PR.
