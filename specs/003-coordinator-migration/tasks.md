<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Tasks: Coordinator Base Class Migration

**Input**: Design documents from
`/specs/003-coordinator-migration/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md,
contracts/coordinator.md, quickstart.md

## Format: `- [ ] T### [P?] [Story...?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story...]**: One or more user story tags this task belongs to
  (e.g., `[US1] [US2]`); optional for meta/polish tasks that span
  stories or have no direct story

## Path Conventions

- **Source**: `custom_components/rental_control/`
- **Tests**: `tests/`

---

## Phase 1: Core Migration (US1 + US2 + US4) 🎯 MVP

**Goal**: Migrate `RentalControlCoordinator` to
`DataUpdateCoordinator[list[CalendarEvent]]`, migrate all entities
to `CoordinatorEntity`, convert error handling to `UpdateFailed`,
and update all callers of removed coordinator APIs — in a single
atomic phase.

**Why single phase**: Removing coordinator APIs (`update()`,
`calendar`, `event_sensors`, `calendar_ready`, `calendar_loaded`)
breaks entity callers. Per Constitution Principle II, every commit
must compile/run successfully. The coordinator base class change,
entity migration, error handling conversion, and caller updates
must land together.

**Independent Test**: The coordinator fetches calendar data on the
configured interval via the DUC scheduler; entities receive updates
through the listener mechanism without polling; errors are reported
via `UpdateFailed` and stale data is preserved; entity availability
reflects coordinator health; `async_config_entry_first_refresh`
populates data before entities are created.

**Acceptance**: SC-001, SC-002, SC-003, SC-004, SC-005, SC-006

### Coordinator Changes

- [x] T001 [US1] Migrate `RentalControlCoordinator` to inherit from `DataUpdateCoordinator[list[CalendarEvent]]` in custom_components/rental_control/coordinator.py: add `super().__init__()` call with `hass`, `_LOGGER`, `name`, `config_entry`, and `update_interval=timedelta(minutes=refresh_frequency)`; remove `self.calendar`, `self.calendar_ready`, `self.calendar_loaded`, `self.next_refresh`, `self.event_sensors`, `self._events_ready` attributes; replace with `self.data` (inherited); preserve all other custom attributes (per R-005, the `refresh_frequency == 0` startup-delay special case is replaced by `async_config_entry_first_refresh()`)
- [x] T002 [US1] [US2] Implement `_async_update_data()` in custom_components/rental_control/coordinator.py: consolidate logic from `_refresh_calendar()` and the refresh-check portion of `update()` into the DUC callback; fetch iCal URL, parse via `_ical_parser()`, update `self.event`, call `event_overrides.async_check_overrides()`, return `list[CalendarEvent]`; preserve miss-tracking (`num_misses`/`max_misses`) by returning previous `self.data` when within tolerance; on fetch errors catch `TimeoutError`, `aiohttp.ClientError`, and `Exception` and re-raise as `UpdateFailed` with descriptive messages so the DUC sets `last_update_success = False` and reschedules retry
- [x] T003 [US1] Implement `_async_setup()` in custom_components/rental_control/coordinator.py: move the Keymaster slot bootstrapping logic from `update()` (the block that reads existing entity states and populates `event_overrides` on first load) into this callback which runs once before the first refresh
- [x] T004 [US1] Remove obsolete methods and attributes from custom_components/rental_control/coordinator.py: delete `update()` method, `_refresh_calendar()` method, `events_ready` property, and all references to removed attributes (`calendar`, `calendar_ready`, `calendar_loaded`, `next_refresh`, `event_sensors`, `_events_ready`)
- [x] T005 [US1] Update `update_config()` in custom_components/rental_control/coordinator.py: replace `self.next_refresh = dt.now()` with logic that sets `self.update_interval` based on the new value followed by `await self.async_request_refresh()`; update `_refresh_event_dict()` to read from `self.data` instead of `self.calendar`
- [x] T006 [US1] Update `update_event_overrides()` in custom_components/rental_control/coordinator.py: replace `self.next_refresh = dt.now()` with `await self.async_request_refresh()`; remove `calendar_ready` flag manipulation
- [x] T007 [US1] Update `async_get_events()` in custom_components/rental_control/coordinator.py: read from `self.data` instead of `self.calendar`
- [x] T008 [US2] Verify stale-data preservation in custom_components/rental_control/coordinator.py: confirm that when `_async_update_data()` raises `UpdateFailed`, DUC does not clear `self.data` — entities continue serving previous calendar events

### Caller Updates

- [x] T009 [US1] Update `async_setup_entry()` in custom_components/rental_control/__init__.py: call `await coordinator.async_config_entry_first_refresh()` after creating coordinator and before forwarding platforms; remove explicit `coordinator.update()` calls from setup path
- [x] T010 [US1] Update `sensor.py` platform setup in custom_components/rental_control/sensor.py: remove the explicit `await coordinator.update()` call before entity creation since `async_config_entry_first_refresh()` already guarantees data
- [x] T011 [US1] Update `event_overrides.py` in custom_components/rental_control/event_overrides.py: replace `coordinator.calendar` with `coordinator.data`; replace `coordinator.calendar_loaded` with `coordinator.data is not None` (preserving the sticky "ever loaded" semantic per data-model.md); replace `coordinator.events_ready` check with appropriate DUC state; remove iteration over `coordinator.event_sensors` (DUC listener mechanism handles entity notification)
- [x] T012 [US1] Update utility functions and listeners: (a) in custom_components/rental_control/util.py update `handle_state_change()` to work with DUC-based coordinator (no `next_refresh` references) and update `get_event_names()` to retrieve event sensor data from `coordinator.data` instead of the removed `coordinator.event_sensors`; (b) in custom_components/rental_control/__init__.py update `update_listener()` to use `coordinator.update_interval` setter instead of `next_refresh`; remove any references to `calendar_ready`, `calendar_loaded`, `events_ready` in both files

### Entity Migration

- [x] T013 [US1] Migrate `RentalControlCalendar` to `CoordinatorEntity` in custom_components/rental_control/calendar.py: change base classes to `(CoordinatorEntity[RentalControlCoordinator], CalendarEntity)`; replace `__init__` to call `super().__init__(coordinator)`; remove `self._available`, `self._event` local state; read `event` from `self.coordinator.event`; remove `async_update()` method; let `CoordinatorEntity` provide `available` and `should_poll`
- [x] T014 [US1] Migrate `RentalControlCalSensor` to `CoordinatorEntity` in custom_components/rental_control/sensors/calsensor.py: change base class from `Entity` to `CoordinatorEntity[RentalControlCoordinator]`; update `__init__` to call `super().__init__(coordinator)`; remove self-registration in `coordinator.event_sensors`; let `CoordinatorEntity` provide `available` and `should_poll`
- [x] T015 [US1] Implement `_handle_coordinator_update()` override in custom_components/rental_control/sensors/calsensor.py: move all logic from the current `async_update()` (Keymaster side effects such as set_code, clear_code, update_times, plus data-reading and state/attribute computation) into this callback or a helper it calls; after local processing, call `super()._handle_coordinator_update()` to trigger `async_write_ha_state()`
- [x] T016 [US1] Eliminate reliance on sensor `async_update()` in custom_components/rental_control/sensors/calsensor.py: refactor the previous `async_update()` implementation into an internal helper used by `_handle_coordinator_update()` for reading `self.coordinator.data[event_number]` and computing state attributes; remove the `async_update()` method or leave as no-op; remove `self.coordinator.calendar_ready` checks and replace with `self.coordinator.last_update_success` via CoordinatorEntity availability semantics
- [x] T017 [US4] Verify entity availability behavior in custom_components/rental_control/calendar.py and custom_components/rental_control/sensors/calsensor.py: confirm `available` property from `CoordinatorEntity` returns `self.coordinator.last_update_success`; verify entities show unavailable when coordinator has never fetched, and available with stale data after a single failure

### Tests

- [x] T018 Update test fixtures in tests/conftest.py (if coordinator fixtures exist) and per-file `_make_coordinator()` helpers in test files: update coordinator construction to use DUC constructor (hass, logger, name, config_entry, update_interval); mock `_async_update_data` instead of `update`/`_refresh_calendar`
- [x] T019 [P] [US1] [US2] Update coordinator unit tests in tests/unit/test_coordinator.py: rewrite tests for `_async_update_data()` replacing tests for `update()`/`_refresh_calendar()`; test DUC lifecycle (`async_config_entry_first_refresh`, `update_interval`, `async_request_refresh`); verify `self.data` replaces `self.calendar`; verify miss-tracking preserves stale data within tolerance; test `UpdateFailed` raised on network timeout, HTTP error (404, 500), and malformed iCal data; test `last_update_success` set to `False` on failure and `True` on recovery
- [x] T020 [P] [US1] Update setup tests in tests/unit/test_init.py: verify `async_config_entry_first_refresh()` is called during setup; verify `ConfigEntryNotReady` raised on first refresh failure
- [x] T021 [P] [US1] Update integration tests in tests/integration/test_refresh_cycle.py: verify DUC-managed scheduling replaces `next_refresh` timestamp comparison; verify `update_interval` property controls refresh timing
- [x] T022 [P] [US1] Update calendar entity tests in tests/unit/test_calendar.py: test `CoordinatorEntity` integration; verify no manual `async_update()` calls; verify `should_poll` is `False`; verify `available` tied to `last_update_success`; verify `event` reads from `coordinator.event`
- [x] T023 [P] [US1] Update sensor entity tests in tests/unit/test_sensors.py: test `CoordinatorEntity` integration; verify no self-registration in `event_sensors`; verify `_handle_coordinator_update()` fires Keymaster side effects; verify `should_poll` is `False`; verify `available` tied to `last_update_success`
- [x] T024 [P] [US4] Add entity availability tests in tests/unit/test_sensors.py and tests/unit/test_calendar.py: test unavailable when coordinator never fetched; test available with stale data after single failure; test recovery to available after sustained failure resolves
- [x] T025 [P] [US2] Update error handling integration tests in tests/integration/test_error_handling.py: test end-to-end error recovery flow; verify entities remain available with stale data during outage; verify entities update when calendar recovers; verify consecutive miss tracking still works within `_async_update_data()`

**Checkpoint**: Coordinator uses DUC base class, entities use
CoordinatorEntity, errors reported via UpdateFailed, all callers
updated, refresh scheduling is platform-managed, first refresh
runs before entities. All tests pass. US1, US2, and US4 complete.

---

## Phase 2: Backward Compatibility & Transparent Migration (US3)

**Goal**: Verify zero regressions — all entity IDs, names, device
registry entries, state attributes, configuration flow, and
Keymaster slot behavior are identical before and after migration.

**Independent Test**: Load a pre-migration config entry, verify all
entities appear with same IDs/names/states; verify Keymaster slot
assignments and door codes remain intact; verify config flow and
stored config data are unchanged.

**Acceptance**: SC-001 (verified end-to-end), SC-002 (verified end-to-end)

### Implementation

- [ ] T026 [US3] Update `async_unload_entry()` in custom_components/rental_control/__init__.py: verify unload path works correctly with DUC-based coordinator; ensure listeners are cleaned up properly
- [ ] T027 [US3] Verify entity identity preservation: confirm unique_id, name, device_info, and extra_state_attributes structure in custom_components/rental_control/calendar.py and custom_components/rental_control/sensors/calsensor.py produce identical values to pre-migration versions
- [ ] T028 [P] [US3] Update util tests in tests/unit/test_util.py: verify `handle_state_change()` works with DUC coordinator; verify `update_listener()` sets `coordinator.update_interval`
- [ ] T029 [P] [US3] Update full setup integration tests in tests/integration/test_full_setup.py: verify complete setup/teardown cycle with DUC coordinator; verify entity IDs, names, device registry entries are unchanged; verify Keymaster slot bootstrapping works in `_async_setup()`; verify configuration flow and stored config data are unaltered (FR-012)
- [ ] T030 [US3] Update event overrides tests in tests/unit/test_event_overrides.py: verify `async_check_overrides()` called from `_async_update_data()` instead of `update()`; verify override integration unchanged; verify `coordinator.data` access replaces `coordinator.calendar`

**Checkpoint**: Zero regressions verified. All entity identities
preserved. Keymaster slot management unchanged. Configuration flow
intact. US3 is complete.

---

## Phase 3: Polish & Verification

**Purpose**: Final validation across all success criteria and edge
cases from the specification

- [ ] T031 Run full test suite and verify coverage ≥ 85% for the `custom_components.rental_control` integration via `uv run pytest tests/ --cov=custom_components.rental_control -x -q`
- [ ] T032 Run full pre-commit pipeline via `uv run pre-commit run --all-files` and fix any issues
- [ ] T033 Verify all success criteria: SC-001 (zero regressions), SC-002 (tests pass + new DUC tests), SC-003 (listener-based updates), SC-004 (stale data on failure), SC-005 (recovery), SC-006 (no custom scheduling), SC-007 (coverage ≥ 85%), SC-008 (pre-commit clean)
- [ ] T034 Verify edge cases from spec: (1) first load with unreachable URL raises ConfigEntryNotReady and retries; (2) refresh interval change via options flow takes effect on next cycle; (3) Keymaster not loaded at startup handled gracefully in `_async_setup()`; (4) mid-refresh shutdown cancels cleanly (DUC cancellation); (5) concurrent refresh requests deduplicated by DUC
- [ ] T035 Update tasks.md to mark all tasks complete

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Core Migration)**: No dependencies — start immediately
- **Phase 2 (Backward Compat)**: Depends on Phase 1 (full migration
  must be in place before regression testing)
- **Phase 3 (Verification)**: Depends on all previous phases

### User Story Dependencies

- **US1 (P1)**: Phase 1 — core migration including coordinator and
  entity changes
- **US2 (P1)**: Phase 1 — error handling via UpdateFailed, delivered
  alongside coordinator migration
- **US3 (P1)**: Phase 2 — backward compatibility verification
- **US4 (P2)**: Phase 1 — entity availability, delivered alongside
  entity migration

### Within Each Phase

- Implementation tasks execute sequentially within each subsection
  (Coordinator Changes → Caller Updates → Entity Migration)
- Test tasks marked [P] can run in parallel with each other
- Each phase ends with a checkpoint validation

### Parallel Opportunities

- T019 through T025 can run in parallel (different test files)
- T028, T029 can run in parallel (different test files)

---

## Parallel Example: Phase 1 Tests

```text
# Launch all Phase 1 tests in parallel:
Task: "Update coordinator unit tests in
  tests/unit/test_coordinator.py"
Task: "Update setup tests in tests/unit/test_init.py"
Task: "Update integration tests in
  tests/integration/test_refresh_cycle.py"
Task: "Update calendar entity tests in
  tests/unit/test_calendar.py"
Task: "Update sensor entity tests in
  tests/unit/test_sensors.py"
Task: "Add entity availability tests"
Task: "Update error handling integration tests in
  tests/integration/test_error_handling.py"
```

---

## Implementation Strategy

### Single Atomic Phase (Phase 1)

1. Complete Phase 1: All coordinator, entity, error handling, and
   caller updates together
2. **STOP and VALIDATE**: Run tests, verify full migration works
3. This delivers SC-001, SC-003, SC-004, SC-005, SC-006
4. This is the MVP — US1, US2, and US4 are complete

### Incremental Verification

1. Phase 1 → Full migration (MVP, all code changes)
2. Phase 2 → Backward compatibility verified (US3 complete)
3. Phase 3 → Final verification across all criteria

---

## Notes

- [P] tasks = different files, no dependencies on incomplete tasks
- [Story] label maps task to user story for traceability
- Each phase is a PR-sized increment per the constitution
- Phase 1 is intentionally large because removing coordinator APIs
  and migrating entity callers must be atomic (Principle II)
- Commit after each task or logical group within the phase
- Stop at any checkpoint to validate independently
- The spec explicitly excludes god-class refactoring — keep
  coordinator methods in place, only change the base class and
  remove custom scheduling/notification logic
