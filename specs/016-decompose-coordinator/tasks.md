<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Tasks: Decompose Coordinator

**Input**: Design documents from `/specs/016-decompose-coordinator/`
**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅,
quickstart.md ✅

**Tests**: Included and required. This feature is a behavior-preserving
refactor of the central `RentalControlCoordinator`, so existing coordinator,
refresh-cycle, event override, reconciliation, calendar, sensor, switch, and
check-in tests remain the primary oracle. New focused tests cover extracted
pure helpers for parsing, reservations, ghost reservations, slot matching,
Keymaster observation/bootstrap/adoption, check-in protection, Store sync,
diagnostics, config updates, and compatibility wrappers.

**Organization**: Tasks are grouped by setup, foundational helper models, the
ordered concern split from PLAN, shell delegation, public compatibility, and
final gates. Implementation must keep
`custom_components/rental_control/coordinator.py` as the public
`RentalControlCoordinator` shell while moving implementation detail into the
internal sibling `custom_components/rental_control/coordinator_helpers/`
package.

## Format: `- [ ] T### [P?] [Story?] Description with file path`

- **[P]**: Can run in parallel (different files, no dependency on incomplete
  tasks)
- **[Story]**: Which user story the task primarily proves (US1 through US5)
- Include exact file paths in descriptions
- Leave every checkbox unchecked until the implementation PR performs the task

## Path Conventions

- **Public shell**: `custom_components/rental_control/coordinator.py`
- **Extracted package**: `custom_components/rental_control/coordinator_helpers/`
- **Production callers that must keep imports unchanged**:
  `custom_components/rental_control/__init__.py`,
  `custom_components/rental_control/calendar.py`,
  `custom_components/rental_control/switch.py`,
  `custom_components/rental_control/sensors/calsensor.py`, and
  `custom_components/rental_control/sensors/checkinsensor.py`
- **Existing coordinator or consumer tests**:
  `tests/unit/test_coordinator.py`,
  `tests/unit/test_coordinator_buffer_update.py`,
  `tests/unit/test_event_overrides.py`,
  `tests/unit/test_keymaster_event_diagnostics.py`,
  `tests/unit/test_slot_reconciliation.py`,
  `tests/unit/test_calendar.py`, `tests/unit/test_sensors.py`,
  `tests/unit/test_switch.py`,
  `tests/integration/test_refresh_cycle.py`,
  `tests/integration/test_checkin_tracking.py`, and
  `tests/integration/test_slot_concurrency.py`
- **New focused tests**:
  `tests/unit/test_coordinator_helper_models.py`,
  `tests/unit/test_coordinator_parsing.py`,
  `tests/unit/test_coordinator_reservations.py`,
  `tests/unit/test_coordinator_observation.py`,
  `tests/unit/test_coordinator_bootstrap.py`,
  `tests/unit/test_coordinator_checkin_protection.py`,
  `tests/unit/test_coordinator_store_sync.py`,
  `tests/unit/test_coordinator_config.py`,
  `tests/unit/test_coordinator_diagnostics.py`, and
  `tests/unit/test_coordinator_imports.py`
- **Feature docs**: `specs/016-decompose-coordinator/`

## Live Module Transition Scope

Implementation changes the live coordinator feature only. The target module
split from PLAN is:

- `custom_components/rental_control/coordinator.py` — public
  `RentalControlCoordinator` shell, Home Assistant lifecycle, config entry
  ownership, calendar fetch I/O, Store writes, Keymaster service calls, refresh
  scheduling, side-effect ordering, public FR-012 members, and compatibility
  wrappers.
- `custom_components/rental_control/coordinator_helpers/__init__.py` — internal
  package marker and typed exports.
- `custom_components/rental_control/coordinator_helpers/models.py` —
  `CalendarParseContext`, `ReservationBuildContext`, `ObservedSlotQuery`,
  `EventOverrideUpdate`, `GhostReservationResult`, `KeymasterSlotSnapshot`,
  bootstrap/adoption decisions, check-in protection snapshots, Store sync plans,
  diagnostics projections, and config snapshots.
- `custom_components/rental_control/coordinator_helpers/calendar_parsing.py` —
  pure iCal event filtering, event-time selection, override-aware conversion,
  `CalendarEvent` construction, UID normalization, and sorting.
- `custom_components/rental_control/coordinator_helpers/reservations.py` —
  regular and ghost reservation builders, display names, aliases, duplicate-name
  grouping, manual PIN preservation, and `GhostReservationResult` mutation plans.
- `custom_components/rental_control/coordinator_helpers/slot_matching.py` —
  `ObservedSlotQuery` matching, duplicate-name/date-window pairing, ordered
  physical subset selection, and physical-name matching helpers.
- `custom_components/rental_control/coordinator_helpers/keymaster_observation.py`
  — pure classification of shell-read HA state snapshots into `ManagedSlot`
  values and actual-state diagnostics.
- `custom_components/rental_control/coordinator_helpers/keymaster_bootstrap.py` —
  pure first-load override setup and cache-only Store adoption decisions.
- `custom_components/rental_control/coordinator_helpers/checkin_protection.py` —
  checked-in/checked-out protection decisions, active-window snapshots, and
  missing physical-stay synthesis decisions.
- `custom_components/rental_control/coordinator_helpers/store_sync.py` —
  cache-only Store mapping mutation plans from `DesiredPlan`, operation results,
  aliases, fingerprints, and diagnostics.
- `custom_components/rental_control/coordinator_helpers/config_update.py` —
  config snapshot parsing, stale override detection, child-lock rediscovery
  decisions, and buffer update payload construction.
- `custom_components/rental_control/coordinator_helpers/diagnostics.py` — latest
  overflow/reconciliation projections and keymaster-event diagnostic records.

No production caller may import from `coordinator_helpers/`; all callers keep
`from .coordinator import RentalControlCoordinator`. The coordinator must keep
using the existing reconciliation package surface: `DesiredPlan`, `ManagedSlot`,
`Reservation`, `SlotStatus`, `compute_desired_plan`, `extract_booking_aliases`,
`make_reservation_fingerprint`, and `normalize_slot_name_for_fingerprint`.

---

## Phase 1: Setup & Baseline (Shared Infrastructure)

**Purpose**: Establish behavior, import, call-site, and complexity baselines
before any production code moves.

- [ ] T001 Run `.specify/scripts/bash/check-prerequisites.sh --json` with `SPECIFY_FEATURE=016-decompose-coordinator` from the repository root and confirm `specs/016-decompose-coordinator/` reports `research.md`, `data-model.md`, and `quickstart.md`
- [ ] T002 Inspect US1-US5, FR-001 through FR-020, edge cases, assumptions, and non-goals in `specs/016-decompose-coordinator/spec.md`
- [ ] T003 Inspect the Project Structure, Concrete Decomposition Design, public compatibility boundary, parameter-reduction sections, FR-018 reconciliation integration, and `aislop` directive removal requirements in `specs/016-decompose-coordinator/plan.md`
- [ ] T004 Inspect all research decisions, all data-model helper objects, and quickstart validation scenarios in `specs/016-decompose-coordinator/research.md`, `specs/016-decompose-coordinator/data-model.md`, and `specs/016-decompose-coordinator/quickstart.md`
- [ ] T005 Inventory `__init__`, `_ical_parser`, `_build_reservations`, `_build_ghost_reservations`, `_observe_managed_slots`, `_find_observed_slot_by_name`, `_apply_checkin_protection`, `async_adopt_keymaster_slots`, `async_setup_keymaster_overrides`, `_sync_slot_store_from_plan`, `_async_update_data`, `update_config`, and `update_event_overrides` in `custom_components/rental_control/coordinator.py`
- [ ] T006 Inventory the exact production caller imports in `custom_components/rental_control/__init__.py`, `custom_components/rental_control/calendar.py`, `custom_components/rental_control/switch.py`, `custom_components/rental_control/sensors/calsensor.py`, and `custom_components/rental_control/sensors/checkinsensor.py`; record that none may change
- [ ] T007 Inventory the current `update_event_overrides` call forms in `custom_components/rental_control/util.py`, `custom_components/rental_control/coordinator.py`, and `tests/unit/test_coordinator.py`
- [ ] T008 Inventory existing coordinator, buffer update, event override, diagnostics, reconciliation, refresh-cycle, calendar, sensor, switch, check-in, and slot concurrency coverage in the test files listed under Path Conventions
- [ ] T009 Run unchanged baseline coordinator parity tests with `uv run pytest tests/unit/test_coordinator.py tests/unit/test_coordinator_buffer_update.py tests/unit/test_event_overrides.py tests/unit/test_keymaster_event_diagnostics.py tests/unit/test_slot_reconciliation.py tests/integration/test_refresh_cycle.py tests/integration/test_slot_concurrency.py -x -q` against the listed test files
- [ ] T010 Run unchanged baseline production-consumer tests with `uv run pytest tests/unit/test_calendar.py tests/unit/test_sensors.py tests/unit/test_switch.py tests/integration/test_checkin_tracking.py -x -q` against the listed test files
- [ ] T011 Record the current line, function-length, and parameter-count baseline for `custom_components/rental_control/coordinator.py`, including the separate `ai-slop/hallucinated-import` and `complexity/file-too-large complexity/function-too-long` `aislop` directives

---

## Phase 2: Foundational Package and Models (Blocking Prerequisites)

**Purpose**: Create the internal helper package and behavior-free dataclasses
before any concern module imports them.

**⚠️ CRITICAL**: No extraction phase can complete until model ownership is
stable. Model modules must not import Home Assistant APIs, Store APIs,
coordinator code, or Keymaster service helpers.

### Foundational Tests

- [ ] T012 [US4] Add internal helper package import and model construction tests for `CalendarParseContext`, `ReservationBuildContext`, `ObservedSlotQuery`, `EventOverrideUpdate`, `GhostReservationResult`, and query defaults in `tests/unit/test_coordinator_helper_models.py`
- [ ] T013 [US4] Add bootstrap/adoption, `KeymasterSlotSnapshot`, `CheckinProtectionSnapshot`, `StoreSyncPlan`, diagnostics projection, and config snapshot construction tests in `tests/unit/test_coordinator_helper_models.py`
- [ ] T014 [US4] Add tests proving model modules are side-effect-free and do not import Home Assistant, Store, coordinator, or Keymaster service modules in `tests/unit/test_coordinator_helper_models.py`

### Foundational Implementation

- [ ] T015 Create `custom_components/rental_control/coordinator_helpers/__init__.py` with SPDX headers, a module docstring, internal typed exports, and no public production caller dependency
- [ ] T016 [US4] Create `custom_components/rental_control/coordinator_helpers/models.py` with `CalendarParseContext`, `ReservationBuildContext`, `ObservedSlotQuery`, `EventOverrideUpdate`, and `GhostReservationResult` dataclasses exactly covering the PLAN fields
- [ ] T017 [US4] Add `KeymasterSlotSnapshot`, `BootstrapDecision`, and `AdoptionMappingDecision` to `custom_components/rental_control/coordinator_helpers/models.py` without raw PIN persistence fields
- [ ] T018 [US4] Add `CheckinProtectionSnapshot`, check-in protection decision types, `StoreSyncPlan`, diagnostics projection types, and config snapshot types to `custom_components/rental_control/coordinator_helpers/models.py`
- [ ] T019 [US4] Ensure `custom_components/rental_control/coordinator_helpers/models.py` uses only behavior-free imports plus existing reconciliation types and keeps every project-owned initializer at no more than six explicit parameters
- [ ] T020 Run foundational validation with `uv run pytest tests/unit/test_coordinator_helper_models.py -q` against the new model tests

**Checkpoint**: The internal package exists, shared dataclasses are stable, and
FR-013/FR-014 parameter bundles are available before modules import them.

---

## Phase 3: Calendar Parsing Extraction (Priority: P1)

**Goal**: Preserve `_ical_parser` and event conversion behavior exactly while
moving pure iCal interpretation into `calendar_parsing.py`.

**Independent Test**: Feed identical `icalendar.Calendar` objects and parsing
contexts into the helper and compare sorted `CalendarEvent` outputs, filtering,
UIDs, summaries, timezones, overrides, and logs to the current coordinator path.

### Tests for Calendar Parsing

- [ ] T021 [US2] Add focused iCal filtering tests for RRULE logging, Smoobu Check-in/Check-out skips, stale events, far-future events, Blocked/Not available filtering, missing descriptions, and sorted output in `tests/unit/test_coordinator_parsing.py`
- [ ] T022 [US2] Add Honor Event Times tests for explicit PMS datetime values, description check-in/check-out times, date-only defaults, disabled override fallback, and manual override fallback in `tests/unit/test_coordinator_parsing.py`
- [ ] T023 [US2] Add timezone, UTF-8 BOM-adjacent parser input, UID normalization, event-prefix mutation, slot-name extraction, and buffer-aware physical override comparison tests in `tests/unit/test_coordinator_parsing.py`

### Implementation for Calendar Parsing

- [ ] T024 [US2] Implement `custom_components/rental_control/coordinator_helpers/calendar_parsing.py` with pure helpers for event filtering, slot-name extraction, override lookup application, event-time selection, timezone conversion, UID normalization, and final sorting
- [ ] T025 [US2] Keep network fetch, timeout handling, response release, executor calls for `Calendar.from_ical` and `x_wr_timezone`, and `UpdateFailed` wrapping in `custom_components/rental_control/coordinator.py` while exposing only fetched calendar objects to parsing helpers
- [ ] T026 [US2] Verify `custom_components/rental_control/coordinator_helpers/calendar_parsing.py` performs no network I/O, Home Assistant state reads, Store writes, refresh requests, or service calls
- [ ] T027 Run calendar parsing validation with `uv run pytest tests/unit/test_coordinator_parsing.py tests/unit/test_coordinator.py -q` against the listed test files

**Checkpoint**: Calendar parsing proves FR-001, FR-002, FR-004, FR-019, and the
calendar portion of SC-style behavior parity without changing fetch boundaries.

---

## Phase 4: Reservation and Ghost Reservation Builders (Priority: P1)

**Goal**: Preserve regular and ghost reservation outputs, identity, aliases,
manual code handling, duplicate-name pairing inputs, and Store mutation plans.

**Independent Test**: Compare helper-built `Reservation` lists and
`GhostReservationResult` plans for identical calendar events, observed slots,
Store mappings, config, generated codes, and active check-in windows.

### Tests for Reservations

- [ ] T028 [US2] Add regular reservation tests for slot-name extraction, display-name formatting, trim/max-name handling, buffer windows, generated codes, invalid reservation skips, sensor lookup keys, and deterministic ordering in `tests/unit/test_coordinator_reservations.py`
- [ ] T029 [US2] Add duplicate-name reservation tests for same starts, shifted dates, ordered date windows, manual observed PIN preservation, code source values, UID aliases, booking aliases, and fingerprint generation in `tests/unit/test_coordinator_reservations.py`
- [ ] T030 [US2] Add ghost reservation tests for missing-count increments, pending-set to pending-clear transitions, status eligibility, physical-name mismatch fencing, invalid date skips, fingerprint history, synthetic identity fields, and raw-PIN redaction in `tests/unit/test_coordinator_reservations.py`

### Implementation for Reservations

- [ ] T031 [US2] Implement regular reservation builders in `custom_components/rental_control/coordinator_helpers/reservations.py` using `ReservationBuildContext`, existing reconciliation helpers, generated-code callbacks, and observed-slot query factories
- [ ] T032 [US2] Implement ghost reservation builders in `custom_components/rental_control/coordinator_helpers/reservations.py` returning `GhostReservationResult` so the coordinator shell applies live Store-cache mutations in the current order
- [ ] T033 [US2] Preserve FR-018 reconciliation integration in `custom_components/rental_control/coordinator_helpers/reservations.py` by importing `Reservation`, `extract_booking_aliases`, `make_reservation_fingerprint`, and `normalize_slot_name_for_fingerprint` from the existing reconciliation package only
- [ ] T034 [US2] Verify `custom_components/rental_control/coordinator_helpers/reservations.py` performs no Home Assistant state reads, Store saves, refresh requests, or Keymaster service calls
- [ ] T035 Run reservation validation with `uv run pytest tests/unit/test_coordinator_reservations.py tests/unit/test_coordinator.py tests/unit/test_slot_reconciliation.py -q` against the listed test files

**Checkpoint**: Reservation extraction proves FR-003, FR-005, FR-006, FR-018,
FR-019, and behavior parity for regular and ghost reservations.

---

## Phase 5: Slot Matching and Keymaster Observation (Priority: P1)

**Goal**: Preserve duplicate-name physical-slot matching and physical Keymaster
state classification while reducing `_find_observed_slot_by_name` through
`ObservedSlotQuery`.

**Independent Test**: Exercise identical managed-slot lists, physical names,
prefixes, date windows, consumed-slot sets, missing dates, unreadable states,
phantom slots, and actual-state diagnostics.

### Tests for Slot Matching and Observation

- [ ] T036 [US3] Add `ObservedSlotQuery` matching tests for exact name, prefixed name, display name, consumed slots, exact date matches, required date matches, shifted-date fallback, unknown-date blocking, expected duplicate counts, and no arbitrary prefix matches in `tests/unit/test_coordinator_observation.py`
- [ ] T037 [US3] Add ordered physical subset and partial pairing tests for duplicate names, duplicate physical names, start/end ordering, minimum-distance matching, and consumed-slot mutation only when a slot is returned in `tests/unit/test_coordinator_observation.py`
- [ ] T038 [US3] Add Keymaster snapshot classification tests for missing entities, unreadable text states, blank text states, occupied slots, free slots, phantom/name-only slots, date-range parsing, enabled-state parsing, and last-error propagation in `tests/unit/test_coordinator_observation.py`
- [ ] T039 [US3] Add actual-state diagnostics tests proving `EventOverrides.update_actual_state()` receives the same redacted fields and is called once per managed slot by the coordinator shell in `tests/unit/test_coordinator_observation.py`

### Implementation for Slot Matching and Observation

- [ ] T040 [P] [US3] Implement `custom_components/rental_control/coordinator_helpers/slot_matching.py` with `find_observed_slot()`, physical-name matching helpers, ordered subset selection, and partial pairing helpers driven by `ObservedSlotQuery`
- [ ] T041 [P] [US3] Implement `custom_components/rental_control/coordinator_helpers/keymaster_observation.py` with pure `KeymasterSlotSnapshot` classification into `ManagedSlot` plus actual-state diagnostics
- [ ] T042 [US3] Preserve prefix stripping and normalized-name behavior in `custom_components/rental_control/coordinator_helpers/slot_matching.py` by using `normalize_slot_name_for_fingerprint` from the reconciliation package root
- [ ] T043 [US3] Keep Home Assistant `hass.states` reads and the ordered `EventOverrides.update_actual_state()` calls in `custom_components/rental_control/coordinator.py`, passing snapshots into `keymaster_observation.py`
- [ ] T044 Run slot matching and observation validation with `uv run pytest tests/unit/test_coordinator_observation.py tests/unit/test_coordinator.py tests/integration/test_refresh_cycle.py -q` against the listed test files

**Checkpoint**: Slot matching and observation prove FR-003, FR-007, FR-013,
FR-018, FR-019, and duplicate-name physical matching parity.

---

## Phase 6: Keymaster Bootstrap and Store Adoption (Priority: P1)

**Goal**: Preserve first-load override setup, partial-reset safety, placeholder
adoption, and cache-only Store adoption without moving async side effects out of
the coordinator shell.

**Independent Test**: Compare bootstrap/adoption decisions, forced-clear
requests, override-update payloads, mapping payloads, skip reasons, persisted
schema metadata, and raw-PIN redaction for identical Keymaster snapshots and
Store caches.

### Tests for Keymaster Bootstrap and Adoption

- [ ] T045 [US3] Add bootstrap decision tests for readable slots, unreadable slots, blank slots, partially reset name-only/date-range-off slots, code-bearing unnamed slots, default date ranges, and override update payloads in `tests/unit/test_coordinator_bootstrap.py`
- [ ] T046 [US3] Add adoption decision tests for empty Store, deleted Store, existing Store mappings, occupied slots, pending-clear slots, unreadable slots, placeholder names, metadata initialization, and raw-PIN redaction in `tests/unit/test_coordinator_bootstrap.py`
- [ ] T047 [US3] Add coordinator-shell bootstrap tests proving `async_fire_clear_code`, `update_event_overrides(..., request_refresh=False)`, `EventOverrides.load_persisted_mappings()`, Store mutation, and save ordering match the current behavior in `tests/unit/test_coordinator_bootstrap.py`

### Implementation for Keymaster Bootstrap and Adoption

- [ ] T048 [US3] Implement pure first-load bootstrap decision helpers in `custom_components/rental_control/coordinator_helpers/keymaster_bootstrap.py` for partially reset slots, placeholders, unreadable skips, default date windows, and `EventOverrideUpdate` payloads
- [ ] T049 [US3] Implement pure adoption mapping helpers in `custom_components/rental_control/coordinator_helpers/keymaster_bootstrap.py` for empty/deleted Store recovery, occupied versus pending-clear mapping status, adopted identity keys, and schema metadata without raw PINs
- [ ] T050 [US3] Keep `async_setup_keymaster_overrides()` and `async_adopt_keymaster_slots()` side effects in `custom_components/rental_control/coordinator.py`, applying `keymaster_bootstrap.py` decisions in the current order
- [ ] T051 Run bootstrap and adoption validation with `uv run pytest tests/unit/test_coordinator_bootstrap.py tests/unit/test_coordinator.py tests/integration/test_refresh_cycle.py -q` against the listed test files

**Checkpoint**: Keymaster bootstrap and adoption prove FR-001, FR-003, FR-008,
FR-011, FR-012, FR-019, and access-safety parity.

---

## Phase 7: Check-in Protection Extraction (Priority: P1)

**Goal**: Preserve check-in sensor driven protection, checked-out marking,
restore-deferral safety, and missing active physical stay synthesis.

**Independent Test**: Feed identical check-in sensor snapshots, reservation
lists, managed slots, buffers, and Store data into the helper and compare
mutation decisions and final reservations to the current coordinator method.

### Tests for Check-in Protection

- [ ] T052 [US1] Add checked-in protection tests for duplicate-name exact start/end matching, unique-name fallback, protected-active flag mutation, same-name physical-slot safety, and generated versus manual observed code source in `tests/unit/test_coordinator_checkin_protection.py`
- [ ] T053 [US1] Add checked-out protection tests for exact match marking, unique match marking, no false positives on future same-name reservations, and sensor lookup key preservation in `tests/unit/test_coordinator_checkin_protection.py`
- [ ] T054 [US1] Add missing active physical stay synthesis and restore-deferral tests for missing dates, stale physical occupants, prefixed names, buffer config changes, and duplicate-name safety in `tests/unit/test_coordinator_checkin_protection.py`

### Implementation for Check-in Protection

- [ ] T055 [US1] Implement `custom_components/rental_control/coordinator_helpers/checkin_protection.py` with pure snapshot extraction inputs, active-window matching, checked-in decisions, checked-out decisions, and synthesized reservation decisions
- [ ] T056 [US1] Keep `hass.data` check-in sensor lookup and live `Reservation` mutation or append ordering in `custom_components/rental_control/coordinator.py`, applying `checkin_protection.py` decisions exactly where `_apply_checkin_protection` runs today
- [ ] T057 [US1] Preserve `_must_defer_for_checkin_restore()` behavior and any active-window helper compatibility in `custom_components/rental_control/coordinator.py` while moving pure decisions to `checkin_protection.py`
- [ ] T058 Run check-in protection validation with `uv run pytest tests/unit/test_coordinator_checkin_protection.py tests/unit/test_coordinator.py tests/integration/test_checkin_tracking.py -q` against the listed test files

**Checkpoint**: Check-in protection proves FR-001, FR-003, FR-009, FR-011,
FR-012, FR-019, and checked-in/checked-out parity.

---

## Phase 8: Store Sync Extraction (Priority: P1)

**Goal**: Preserve cache-only Store mapping updates from desired plans and
operation results while leaving Store writes and `EventOverrides` reloads in the
shell.

**Independent Test**: Compare mutation plans and final `_slot_mappings` for
confirmed clears, failed sets, selected reservations, aliases, fingerprints,
latest-plan metadata, stale physical-slot keys, and migration fields.

### Tests for Store Sync

- [ ] T059 [US3] Add Store sync tests for confirmed-clear removals before upserts, stale same-physical-slot key removals, selected reservation upserts, alias/fingerprint retention, latest-plan metadata, and migration fields in `tests/unit/test_coordinator_store_sync.py`
- [ ] T060 [US3] Add Store sync tests proving failed sets do not advance assignment metadata and Store remains cache-only when physical Keymaster state and calendar data disagree in `tests/unit/test_coordinator_store_sync.py`

### Implementation for Store Sync

- [ ] T061 [US3] Implement `custom_components/rental_control/coordinator_helpers/store_sync.py` with pure `StoreSyncPlan` construction from `DesiredPlan`, `Reservation` lookup, operation results, existing mappings, and coordinator metadata
- [ ] T062 [US3] Keep live `_slot_mappings` mutation, `EventOverrides.load_persisted_mappings()`, `async_save_slot_store()`, and save ordering in `custom_components/rental_control/coordinator.py`
- [ ] T063 Run Store sync validation with `uv run pytest tests/unit/test_coordinator_store_sync.py tests/unit/test_coordinator.py tests/unit/test_event_overrides.py tests/integration/test_refresh_cycle.py -q` against the listed test files

**Checkpoint**: Store sync proves FR-001, FR-003, FR-008, FR-010, FR-018,
FR-019, and cache-only Store semantics.

---

## Phase 9: Diagnostics and Config Update Extraction (Priority: P1)

**Goal**: Preserve diagnostics projections, keymaster event-recording, config
updates, child-lock rediscovery, and buffer-time update behavior.

**Independent Test**: Compare diagnostics dictionaries, redaction, ring-buffer
records, check-in sensor writes, config-field mutations, EventOverrides
recreation, buffer datetime service calls, and refresh requests for identical
inputs.

### Tests for Diagnostics and Config

- [ ] T064 [US1] Add diagnostics projection tests for `latest_overflow`, `latest_reconciliation_diagnostics`, actual-state snapshots, raw-code redaction, `keymaster_event_diagnostics` ordering, and check-in sensor `async_write_ha_state()` triggers in `tests/unit/test_coordinator_diagnostics.py`
- [ ] T065 [US4] Add config update tests for in-place field updates, stale `EventOverrides` detection on lock/range/capacity changes, child-lock rediscovery, persisted mapping reload, buffer change detection, and final refresh request in `tests/unit/test_coordinator_config.py`
- [ ] T066 [US4] Add buffer update payload tests for old-buffer reversal, new-buffer application, assigned-slot filtering, datetime service call arguments, exception handling, override-cache advancement, and gather-result logging in `tests/unit/test_coordinator_config.py`

### Implementation for Diagnostics and Config

- [ ] T067 [P] [US1] Implement `custom_components/rental_control/coordinator_helpers/diagnostics.py` with pure latest-plan projection helpers, redacted keymaster-event record helpers, and diagnostics snapshot shape preservation
- [ ] T068 [P] [US4] Implement `custom_components/rental_control/coordinator_helpers/config_update.py` with config snapshots, stale override decisions, child-lock reset decisions, and buffer update payload construction
- [ ] T069 [US1] Keep diagnostics ring-buffer ownership, listener callback side effects, and check-in sensor state writes in `custom_components/rental_control/coordinator.py` while using `diagnostics.py` projections
- [ ] T070 [US4] Keep `update_config()` shell side effects, `EventOverrides` recreation, `async_setup_keymaster_overrides()`, mapping reload, child-lock discovery, `_async_update_buffer_times()`, and `async_request_refresh()` ordering in `custom_components/rental_control/coordinator.py`
- [ ] T071 Run diagnostics and config validation with `uv run pytest tests/unit/test_coordinator_diagnostics.py tests/unit/test_coordinator_config.py tests/unit/test_coordinator_buffer_update.py tests/unit/test_keymaster_event_diagnostics.py tests/unit/test_coordinator.py -q` against the listed test files

**Checkpoint**: Diagnostics and config prove FR-001, FR-010, FR-011, FR-012,
FR-019, and runtime-update parity.

---

## Phase 10: Shell Delegation and Public Compatibility (Priority: P1) 🎯 MVP

**Goal**: Slim `coordinator.py` into the public Home Assistant shell with all
FR-012 members intact, all production caller imports unchanged, and compatibility
wrappers satisfying parameter limits.

**Independent Test**: Run existing coordinator and production-consumer tests
unchanged, exercise every public wrapper call style, and prove no production
caller imports from `coordinator_helpers/`.

### Tests for Shell Compatibility

- [ ] T072 [US4] Add `_find_observed_slot_by_name` compatibility tests for the current three-argument call, direct `ObservedSlotQuery` input, and legacy keyword criteria including consumed slots, dates, date-window options, and expected-name count in `tests/unit/test_coordinator.py`
- [ ] T073 [US4] Add `update_event_overrides` compatibility tests for direct `EventOverrideUpdate`, the current five-positional util.py call, the current five-positional plus `request_refresh=False` bootstrap call, current keyword tests, missing values, duplicate values, and unknown keyword rejection in `tests/unit/test_coordinator.py`
- [ ] T074 [US4] Add public import tests proving `RentalControlCoordinator` imports from `custom_components.rental_control.coordinator` and production import sites in `__init__.py`, `calendar.py`, `switch.py`, `sensors/calsensor.py`, and `sensors/checkinsensor.py` require no edits in `tests/unit/test_coordinator_imports.py`
- [ ] T075 [US4] Add FR-012 public member tests for `monitored_locknames`, `device_info`, `entry_id`, `unique_id`, `version`, `latest_plan`, diagnostics properties, Store methods, Keymaster methods, config fields, `event`, and `event_overrides` in `tests/unit/test_coordinator_imports.py`
- [ ] T076 [US1] Add shell orchestration parity tests for `_async_update_data` order: fetch/cache fallback, miss tolerance, current-event selection, observation, reservation building, check-in protection, one `compute_desired_plan()` call, apply-plan, Store sync, latest-plan assignment, Store save, and child-lock rediscovery in `tests/unit/test_coordinator.py`

### Implementation for Shell Delegation

- [ ] T077 [US4] Update `custom_components/rental_control/coordinator.py` imports to use `coordinator_helpers/` internally while keeping `RentalControlCoordinator` defined in the same file and avoiding any production caller import changes
- [ ] T078 [US4] Refactor `RentalControlCoordinator.__init__` in `custom_components/rental_control/coordinator.py` to delegate repeated config parsing to `config_update.py` while preserving construction side effects, public attributes, `EventOverrides` setup, child-lock discovery, and `DataUpdateCoordinator` initialization order
- [ ] T079 [US2] Replace `_ical_parser` in `custom_components/rental_control/coordinator.py` with a wrapper below 80 lines that builds `CalendarParseContext`, delegates to `calendar_parsing.py`, and preserves `_async_fetch_calendar` I/O/executor boundaries
- [ ] T080 [US2] Replace `_build_reservations` and `_build_ghost_reservations` in `custom_components/rental_control/coordinator.py` with wrappers below 80 lines that build contexts, delegate to `reservations.py`, and apply `GhostReservationResult` mutations in the current order
- [ ] T081 [US3] Replace `_observe_managed_slots` in `custom_components/rental_control/coordinator.py` with a wrapper below 80 lines that reads HA states, creates `KeymasterSlotSnapshot` values, delegates classification, and calls `EventOverrides.update_actual_state()` once per slot in current order
- [ ] T082 [US3] Replace `_find_observed_slot_by_name` in `custom_components/rental_control/coordinator.py` with a no-more-than-six-parameter compatibility wrapper that normalizes `ObservedSlotQuery`, current three-argument calls, and legacy keyword criteria before delegating to `slot_matching.py`
- [ ] T083 [US3] Replace `async_setup_keymaster_overrides` and `async_adopt_keymaster_slots` in `custom_components/rental_control/coordinator.py` with wrappers below 80 lines that apply `keymaster_bootstrap.py` decisions while retaining async service calls, Store mutation, mapping reload, and save ordering
- [ ] T084 [US1] Replace `_apply_checkin_protection` and restore-deferral decision paths in `custom_components/rental_control/coordinator.py` with wrappers below 80 lines that apply `checkin_protection.py` decisions to live `Reservation` objects in the current sequence
- [ ] T085 [US3] Replace `_sync_slot_store_from_plan` in `custom_components/rental_control/coordinator.py` with a wrapper below 80 lines that applies `store_sync.py` mutation plans and reloads `EventOverrides` mappings exactly as before
- [ ] T086 [US1] Wire diagnostics projections and event-recording helpers from `diagnostics.py` into `custom_components/rental_control/coordinator.py` without changing `latest_overflow`, `latest_reconciliation_diagnostics`, `keymaster_event_diagnostics`, raw-code redaction, or check-in sensor write behavior
- [ ] T087 [US4] Slim `update_config` and buffer-update paths in `custom_components/rental_control/coordinator.py` with `config_update.py` helpers while preserving stale override recreation, setup ordering, mapping reload, buffer service calls, child-lock discovery, and refresh request behavior
- [ ] T088 [US4] Replace `update_event_overrides` in `custom_components/rental_control/coordinator.py` with `async def update_event_overrides(self, update=None, *values, request_refresh=True, **legacy)` or an equivalent no-more-than-six-parameter wrapper normalizing all accepted call forms to `EventOverrideUpdate`
- [ ] T089 [US1] Slim `_async_update_data` in `custom_components/rental_control/coordinator.py` below 80 lines as the refresh-cycle orchestrator while preserving side-effect order and keeping HA I/O, Store writes, service calls, refresh scheduling, and latest-state assignment in the shell
- [ ] T090 [US4] Verify every FR-012 member remains on `RentalControlCoordinator` in `custom_components/rental_control/coordinator.py` with behavior-compatible semantics and no move to `coordinator_helpers/` as a public contract
- [ ] T091 [US4] Verify no production caller import changed in `custom_components/rental_control/__init__.py`, `custom_components/rental_control/calendar.py`, `custom_components/rental_control/switch.py`, `custom_components/rental_control/sensors/calsensor.py`, and `custom_components/rental_control/sensors/checkinsensor.py`; any diff in those import lines must be reverted
- [ ] T092 Run shell compatibility validation with `uv run pytest tests/unit/test_coordinator.py tests/unit/test_coordinator_imports.py tests/unit/test_calendar.py tests/unit/test_sensors.py tests/unit/test_switch.py tests/integration/test_checkin_tracking.py -q` against the listed test files

**Checkpoint**: Shell delegation proves FR-001, FR-002, FR-003, FR-011,
FR-012, FR-013, FR-014, FR-017, FR-018, FR-019, and public compatibility.

---

## Phase 11: Cleanup, Complexity, and Acceptance Gates

**Purpose**: Remove temporary extraction scaffolding, prove behavior parity,
measure complexity immediately before directive removal, remove only the
complexity suppression, and run final quality gates.

### Cleanup and Complexity Gates

- [ ] T093 [US5] Remove temporary extraction shims from `custom_components/rental_control/coordinator.py` and `custom_components/rental_control/coordinator_helpers/*.py`, leaving only planned wrappers, FR-012 public members, and intentional helper exports
- [ ] T094 [US5] Confirm no new calendar parsing rules, lock-code business rules, reconciliation behavior, sensors, automations, configuration options, diagnostics fields, Home Assistant refreshes, Store authority, state writes, blocking I/O, or user-visible delays were introduced in `custom_components/rental_control/coordinator.py` and `custom_components/rental_control/coordinator_helpers/*.py`
- [ ] T095 [US5] Immediately before removing the complexity directive, measure `custom_components/rental_control/coordinator.py` and every `custom_components/rental_control/coordinator_helpers/*.py` file with `wc -l` or the quickstart Python snippet and confirm every file is below 400 lines
- [ ] T096 [US5] Immediately before removing the complexity directive, run `uv run pre-commit run aislop` and confirm every in-scope function is below 80 lines, every project-owned parameter list is no more than six parameters, and no replacement complexity suppression is needed
- [ ] T097 [US5] Remove only `# aislop-ignore-file complexity/file-too-large complexity/function-too-long -- Existing module size is outside this emergency fix scope.` from `custom_components/rental_control/coordinator.py` after T095 and T096 pass
- [ ] T098 [US5] Verify `# aislop-ignore-file ai-slop/hallucinated-import -- Provided by Home Assistant runtime.` is still present in `custom_components/rental_control/coordinator.py` and the complexity/file-size/function-length directive is absent
- [ ] T099 [US5] Verify every FR-001 through FR-020 and every success criterion or quickstart validation scenario has a test, implementation, or acceptance task mapped in `specs/016-decompose-coordinator/tasks.md`

### Acceptance and Quality Gates

- [ ] T100 Run unchanged existing coordinator parity tests with `uv run pytest tests/unit/test_coordinator.py tests/unit/test_coordinator_buffer_update.py tests/unit/test_event_overrides.py tests/unit/test_keymaster_event_diagnostics.py tests/unit/test_slot_reconciliation.py tests/integration/test_refresh_cycle.py tests/integration/test_slot_concurrency.py -x -q` against the listed test files
- [ ] T101 Run production-consumer tests with `uv run pytest tests/unit/test_calendar.py tests/unit/test_sensors.py tests/unit/test_switch.py tests/integration/test_checkin_tracking.py -x -q` against the listed test files
- [ ] T102 Run all new focused helper tests with `uv run pytest tests/unit/test_coordinator_helper_models.py tests/unit/test_coordinator_parsing.py tests/unit/test_coordinator_reservations.py tests/unit/test_coordinator_observation.py tests/unit/test_coordinator_bootstrap.py tests/unit/test_coordinator_checkin_protection.py tests/unit/test_coordinator_store_sync.py tests/unit/test_coordinator_config.py tests/unit/test_coordinator_diagnostics.py tests/unit/test_coordinator_imports.py -q` against the listed test files
- [ ] T103 Verify `update_event_overrides` compatibility through tests and/or code review for direct `EventOverrideUpdate`, five positional util.py calls, five positional plus `request_refresh=False` bootstrap calls, and current keyword test calls in `tests/unit/test_coordinator.py`
- [ ] T104 Verify `_find_observed_slot_by_name` compatibility through tests and/or code review for direct `ObservedSlotQuery`, the current three-argument test call, and legacy keyword criteria in `tests/unit/test_coordinator.py`
- [ ] T105 Verify FR-018 reconciliation integration by confirming `custom_components/rental_control/coordinator.py` and `custom_components/rental_control/coordinator_helpers/*.py` continue using `DesiredPlan`, `ManagedSlot`, `Reservation`, `SlotStatus`, `compute_desired_plan`, `extract_booking_aliases`, `make_reservation_fingerprint`, and `normalize_slot_name_for_fingerprint` from `custom_components/rental_control/reconciliation/`
- [ ] T106 Run full regression tests with `uv run pytest tests/ -x -q` against `tests/`
- [ ] T107 Run linting with `uv run ruff check custom_components/ tests/` against `custom_components/` and `tests/`
- [ ] T108 Run isolated complexity validation with `uv run pre-commit run aislop` against the staged implementation files
- [ ] T109 Run full pre-commit validation with `uv run pre-commit run --all-files` against repository-tracked files, including reuse, yamllint, actionlint, aislop, ruff, ruff-format, mypy, interrogate, and gitlint hooks
- [ ] T110 Review `specs/016-decompose-coordinator/quickstart.md` and confirm the implementation PR notes list unchanged parity commands, new focused helper commands, caller-import verification, both compatibility wrapper call-form gates, hot-path safeguards, file-size measurement before directive removal, final `aislop` results, and final validation results

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup & Baseline (Phase 1)**: No dependencies.
- **Foundational Package and Models (Phase 2)**: Depends on Setup and blocks all
  extraction work because helper modules import shared dataclasses.
- **Calendar Parsing (Phase 3)**: Depends on model ownership and can be helper
  tested before coordinator shell delegation.
- **Reservations (Phase 4)**: Depends on models and uses reconciliation package
  helpers; final manual-PIN behavior depends on slot matching.
- **Slot Matching and Observation (Phase 5)**: Depends on models; reservations,
  check-in protection, and shell wrappers depend on `ObservedSlotQuery` and
  physical matching helpers.
- **Keymaster Bootstrap and Adoption (Phase 6)**: Depends on Keymaster snapshots
  and `EventOverrideUpdate` models.
- **Check-in Protection (Phase 7)**: Depends on reservations and slot matching.
- **Store Sync (Phase 8)**: Depends on desired-plan outputs, reservation lookup,
  and operation-result behavior.
- **Diagnostics and Config (Phase 9)**: Depends on models and existing shell
  behavior; final shell wiring depends on these helper boundaries.
- **Shell Delegation and Public Compatibility (Phase 10)**: Depends on all owner
  modules. It performs the sequential `coordinator.py` work, wrapper
  compatibility, and caller-import verification.
- **Cleanup and Acceptance Gates (Phase 11)**: Depends on all extraction and shell
  phases. File-size measurement and `aislop` checks happen immediately before
  the complexity directive is removed.

### User Story Dependencies

- **US1 (P1)**: Refresh-cycle behavior parity starts with parsing, reservations,
  observation, check-in protection, Store sync, diagnostics, and shell
  orchestration; this is the MVP safety gate.
- **US2 (P1)**: Calendar parsing, regular reservations, and ghost reservations
  can be helper tested after models and before final shell delegation.
- **US3 (P1)**: Keymaster observation, setup, adoption, Store sync, and
  reconciliation integration depend on snapshots and matching helpers.
- **US4 (P1)**: Public coordinator surface and parameter compatibility depend on
  all helper modules and finish before cleanup.
- **US5 (P2)**: Maintainability follows extraction because final file/function
  thresholds, shim removal, and directive removal are meaningful only after the
  shell is slimmed.

### Within Each Story

- Focused tests are written before the corresponding helper extraction tasks and
  should fail or expose missing coverage until the extraction lands.
- `coordinator_helpers/models.py` precedes every helper module that imports
  `CalendarParseContext`, `ReservationBuildContext`, `ObservedSlotQuery`,
  `EventOverrideUpdate`, `GhostReservationResult`, `KeymasterSlotSnapshot`, or
  Store/config/diagnostic decision types.
- `calendar_parsing.py` and `reservations.py` can be developed as pure helpers
  before `coordinator.py` delegates to them.
- `slot_matching.py` precedes reservation manual-PIN matching, check-in
  protection, and the `_find_observed_slot_by_name` wrapper.
- `keymaster_observation.py` precedes bootstrap/adoption and refresh-cycle shell
  wiring that needs `ManagedSlot` observations.
- `EventOverrideUpdate` tests must cover dataclass input, five positional util.py
  calls, five positional plus `request_refresh=False`, and keyword calls before
  the wrapper is considered complete.
- Production caller import verification happens after shell wrappers are wired
  and before final cleanup.
- Temporary extraction shims are removed before measuring for directive removal.
- The complexity/file-size/function-length directive is removed only after
  immediate file-size measurement and `uv run pre-commit run aislop` pass; the
  hallucinated-import directive remains.

---

## Parallel Opportunities

- T040 and T041 can run in parallel after models because slot matching and
  Keymaster observation own different helper modules.
- T067 and T068 can run in parallel because diagnostics and config helpers own
  different modules and test files.
- Focused test files for parsing, reservations, observation, bootstrap,
  check-in protection, Store sync, config, diagnostics, and imports can be
  developed in parallel after T020 if each contributor owns a different file.
- T100, T101, and T102 can run independently once implementation is complete;
  T106 through T109 are final serial quality gates.

## Parallel Example: Helper Work After Models

```bash
Task: "Add iCal helper parity tests in tests/unit/test_coordinator_parsing.py"
Task: "Add Keymaster observation tests in tests/unit/test_coordinator_observation.py"
Task: "Implement diagnostics helpers in custom_components/rental_control/coordinator_helpers/diagnostics.py"
```

---

## Implementation Strategy

### MVP First (Refresh-Cycle Safety)

1. Complete Phase 1 and Phase 2.
2. Add focused tests and pure helpers for calendar parsing, reservations, slot
   matching, observation, Keymaster bootstrap/adoption, check-in protection, and
   Store sync.
3. Wire `coordinator.py` as the shell and validate `_async_update_data` output
   and side-effect order with unchanged existing tests.
4. Stop and review behavior parity before removing shims or the complexity
   directive.

### Incremental Delivery

1. Build behavior-free models and helper package exports.
2. Extract pure parsing and reservation builders, including ghost reservation
   mutation plans.
3. Extract slot matching and Keymaster observation before bootstrapping and
   check-in protection depend on them.
4. Extract bootstrap/adoption, Store sync, diagnostics, and config decisions while
   leaving async side effects in the shell.
5. Slim `coordinator.py` wrappers sequentially, add parameter-bundle
   compatibility, and prove caller imports are unchanged.
6. Remove temporary shims, measure all in-scope files immediately before removing
   the complexity directive, keep the hallucinated-import directive, and run full
   gates.

---

## Acceptance Coverage Map

| Coverage item | Primary tasks |
|---------------|---------------|
| US1 refresh-cycle behavior parity | T008-T010, T052-T058, T059-T071, T076, T084-T089, T092, T100-T102 |
| US2 calendar and reservation parity | T021-T035, T079-T080, T100, T102 |
| US3 Keymaster observation/setup/adoption | T036-T051, T059-T063, T081-T083, T100-T102 |
| US4 public coordinator surface | T006-T007, T012-T020, T072-T075, T077-T078, T082, T088, T090-T092, T103-T105 |
| US5 maintainability under aislop | T011, T019, T089, T093-T099, T108-T109 |
| FR-001 observable behavior unchanged | T009-T010, T027, T035, T044, T051, T058, T063, T071, T092, T100-T102 |
| FR-002 existing tests unchanged | T008-T010, T027, T035, T044, T051, T058, T063, T071, T092, T100-T101 |
| FR-003 refresh outputs equivalent | T028-T044, T052-T063, T076, T084-T089, T100 |
| FR-004 calendar parsing equivalent | T021-T027, T079, T100, T102 |
| FR-005 reservation building equivalent | T028-T035, T080, T102 |
| FR-006 ghost reservations equivalent | T030-T035, T080, T102 |
| FR-007 physical observation equivalent | T036-T044, T081, T100-T102 |
| FR-008 setup/adoption equivalent | T045-T051, T083, T100-T102 |
| FR-009 check-in protection equivalent | T052-T058, T084, T101-T102 |
| FR-010 diagnostics equivalent | T064, T067, T069, T086, T100-T102 |
| FR-011 public coordinator surface | T006, T072-T075, T090-T092, T101 |
| FR-012 consumed members retained | T075, T090, T092, T101 |
| FR-013 `_find_observed_slot_by_name` parameter reduction | T012, T036-T044, T072, T082, T104, T108 |
| FR-014 `update_event_overrides` parameter reduction | T007, T012, T045-T051, T073, T088, T103, T108 |
| FR-015 directive removal precision | T011, T095-T098, T108-T109 |
| FR-016 file/function/parameter limits | T011, T019, T078-T089, T095-T096, T108-T109 |
| FR-017 coordinator remains shell | T025-T026, T043, T050, T056-T057, T062, T069-T070, T077-T090 |
| FR-018 reconciliation integration | T033, T042, T061, T089, T105 |
| FR-019 no new hot-path side effects | T026, T034, T043, T050, T056, T062, T069-T070, T089, T094 |
| FR-020 behavior-preserving docs | T002-T004, T094, T099, T110 |
| Success: existing tests green | T009-T010, T100-T101, T106 |
| Success: focused helper coverage | T012-T014, T021-T023, T028-T030, T036-T039, T045-T047, T052-T054, T059-T060, T064-T066, T102 |
| Success: caller compatibility | T006-T007, T072-T075, T088, T090-T092, T103-T104 |
| Success: complexity thresholds | T095-T098, T108-T109 |
| Success: docs-only tasks stage | This `tasks.md` PR only; implementation tasks start unchecked |
