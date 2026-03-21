<!--
SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Tasks: Guest Check-in/Check-out Tracking

**Input**: Design documents from `/specs/004-checkin-tracking/`
**Prerequisites**: plan.md ✅, spec.md ✅, data-model.md ✅, contracts/ ✅, research.md ✅, quickstart.md ✅

**Tests**: Included — plan.md requires unit + integration tests for all new code (Constitution Principle I); quickstart.md defines test files; markers: `unit`, `integration`.

**Organization**: Tasks grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **Integration root**: `custom_components/rental_control/`
- **Sensors subdir**: `custom_components/rental_control/sensors/`
- **Tests root**: `tests/`
- **Fixtures**: `tests/fixtures/`
- **Unit tests**: `tests/unit/`
- **Integration tests**: `tests/integration/`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Add constants, platform registration, and test infrastructure needed by all user stories.

- [ ] T001 Add check-in tracking constants to `custom_components/rental_control/const.py`: add `SWITCH = "switch"` platform constant, append `SWITCH` to `PLATFORMS` list, add state constants (`CHECKIN_STATE_NO_RESERVATION = "no_reservation"`, `CHECKIN_STATE_AWAITING = "awaiting_checkin"`, `CHECKIN_STATE_CHECKED_IN = "checked_in"`, `CHECKIN_STATE_CHECKED_OUT = "checked_out"`), add event names (`EVENT_RENTAL_CONTROL_CHECKIN = "rental_control_checkin"`, `EVENT_RENTAL_CONTROL_CHECKOUT = "rental_control_checkout"`), add config key `CONF_CLEANING_WINDOW = "cleaning_window"` with `DEFAULT_CLEANING_WINDOW = 6.0`, and add `EARLY_CHECKOUT_GRACE_MINUTES = 15` constant
- [ ] T002 [P] Create test fixture data in `tests/fixtures/checkin_data.py`: define helper functions that return sample coordinator event data as `list[homeassistant.components.calendar.CalendarEvent]` (matching `RentalControlCoordinator.data`) for test scenarios — single event (future start), active event (start in past, end in future), past event, same-day turnover pair (event 0 ends today, event 1 starts today), different-day follow-on pair, and no-events empty list; include timezone-aware datetime objects using `dt_util`
- [ ] T003 [P] Update `tests/conftest.py`: add checkin-specific fixtures — `mock_checkin_coordinator` (coordinator with configurable event data, `lockname`, `start_slot`, `max_events`, `checkin`/`checkout` times, and `unique_id`), `mock_config_entry` with `CONF_CLEANING_WINDOW` in options, and `mock_hass` with event bus mock for event verification

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Create entity skeletons and platform wiring that ALL user stories build upon.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [ ] T004 Create `custom_components/rental_control/sensors/checkinsensor.py` with `CheckinTrackingSensor` skeleton class inheriting from `CoordinatorEntity` and `RestoreEntity`: define `__init__` accepting coordinator and config_entry, set `_attr_native_value` to `CHECKIN_STATE_NO_RESERVATION`, declare all instance variables from data-model.md (`_tracked_event_summary`, `_tracked_event_start`, `_tracked_event_end`, `_tracked_event_slot_name`, `_checkin_source`, `_checkout_source`, `_checkout_time`, `_transition_target_time`, `_checked_out_event_key`) plus `_unsub_timer` as an internal timer unsubscribe handle, implement `unique_id` property using `gen_uuid(f"{coordinator.unique_id} checkin_tracking")`, implement `device_info` linking to existing integration device, implement `extra_state_attributes` returning all attribute fields per data-model.md, and add `_event_key()` static method returning `f"{summary}|{start.isoformat()}"` for event identity tracking
- [ ] T005 Register `CheckinTrackingSensor` in `custom_components/rental_control/sensor.py`: in `async_setup_entry()`, import `CheckinTrackingSensor` from `sensors.checkinsensor`, instantiate it with the coordinator and config_entry, and append it to the entities list passed to `async_add_entities`; the sensor must be created for every integration instance regardless of keymaster configuration (FR-028)
- [ ] T006 Update `custom_components/rental_control/sensors/__init__.py` to export `CheckinTrackingSensor` from `checkinsensor` module

**Checkpoint**: Skeleton sensor entity loads with HA, displays `no_reservation`, and exposes empty attributes. No state transitions yet.

---

## Phase 3: User Story 1 — Core Occupancy Tracking (Priority: P1) 🎯 MVP

**Goal**: Sensor automatically transitions through all four states (`no_reservation` → `awaiting_checkin` → `checked_in` → `checked_out` → next state) based on coordinator event data and timer-scheduled callbacks, firing HA event bus events on each check-in and check-out transition.

**Independent Test**: Configure integration with a calendar containing events, no keymaster. Sensor transitions through all states based on event timing; `rental_control_checkin` and `rental_control_checkout` events fire on transitions.

### Tests for User Story 1 ⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [ ] T007 [US1] Write unit tests for state machine transitions in `tests/unit/test_checkin_sensor.py`: test `no_reservation` → `awaiting_checkin` when coordinator provides a relevant event, test `awaiting_checkin` → `checked_in` at event start time (keymaster monitoring disabled), test `checked_in` → `checked_out` at event end time, test `checked_out` → `no_reservation` after cleaning window (FR-006b), test that sensor stays in `no_reservation` when coordinator has no events, and test that sensor attributes update with event data on each transition. **Note**: Calendar fetch failure with cached data does not require a dedicated test here — this scenario is covered by the existing coordinator-level stale-data preservation mechanism which continues serving cached event data on fetch failure.
- [ ] T008 [US1] Write unit tests for event identity and FR-007 in `tests/unit/test_checkin_sensor.py`: test that `_event_key()` generates correct composite key from summary and start time, test that once in `checked_out` for an event the sensor does NOT re-transition to `checked_in` when the same event's end time is extended, and test that a genuinely new event (different key) DOES trigger a new `awaiting_checkin` cycle
- [ ] T009 [US1] Write unit tests for HA event bus firing in `tests/unit/test_checkin_sensor.py`: test that `rental_control_checkin` event fires with correct payload (entity_id, summary, start, end, guest_name, source=`automatic`) on check-in transition, and test that `rental_control_checkout` event fires with correct payload (source=`automatic`) on check-out transition; verify event data matches contracts/events.md schema

### Implementation for User Story 1

- [ ] T010 [US1] Implement `_handle_coordinator_update()` in `custom_components/rental_control/sensors/checkinsensor.py`: on coordinator data update, get `coordinator.data[0]` as the most relevant event (FR-002), extract `summary`, `start`, and `end` from the `CalendarEvent` and derive the guest/slot name via the existing `get_slot_name(summary, description, prefix)` helper (as done in `sensors/calsensor.py`), evaluate whether a state transition is needed based on current state and event timing, and call the appropriate transition method; when in `no_reservation` and a relevant event is found transition to `awaiting_checkin`; when event disappears from coordinator data transition to `no_reservation`. **Note**: Overlapping with event overrides is a read-only concern — the check-in tracking sensor does not write to event_overrides, so no dedicated test is needed for this interaction.
- [ ] T011 [US1] Implement timer-based auto transitions in `custom_components/rental_control/sensors/checkinsensor.py`: use `async_track_point_in_time()` to schedule callbacks — schedule auto check-in at event start time when entering `awaiting_checkin` (time-based mode, i.e., keymaster monitoring not enabled), schedule auto check-out at event end time when entering `checked_in`, cancel any existing timer via stored `_unsub_timer` before scheduling a new one; implement `_async_timer_callback()` that executes the scheduled transition
- [ ] T011a [US1] Implement FR-030 auto check-out rescheduling in `custom_components/rental_control/sensors/checkinsensor.py`: when `_handle_coordinator_update()` runs while the sensor is in `checked_in` and the currently tracked event (matching the existing event identity key via `_event_key(summary, start)`) has a different `end` time than `_tracked_event_end`, update the stored `_tracked_event_end` and re-schedule the pending auto check-out timer by cancelling `_unsub_timer` and creating a new `async_track_point_in_time()` callback for the new end time; ensure that updates which do not change `end` do not re-schedule the timer
- [ ] T012 [US1] Implement state transition methods in `custom_components/rental_control/sensors/checkinsensor.py`: `_transition_to_awaiting(event_data)` sets state and attributes from event, `_transition_to_checked_in(source)` sets state and records checkin_source, `_transition_to_checked_out(source)` sets state and records checkout_source/time, `_transition_to_no_reservation()` clears all tracked event data; each transition calls `self.async_write_ha_state()` and schedules next timer
- [ ] T013 [US1] Implement event bus firing in `custom_components/rental_control/sensors/checkinsensor.py`: in `_transition_to_checked_in()` fire `rental_control_checkin` event via `self.hass.bus.async_fire()` with payload matching contracts/events.md (entity_id, summary, start ISO 8601, end ISO 8601, guest_name, source); in `_transition_to_checked_out()` fire `rental_control_checkout` event with matching payload
- [ ] T014 [US1] Implement post-checkout linger timing in `custom_components/rental_control/sensors/checkinsensor.py`: in `_transition_to_checked_out()`, examine `coordinator.data[1]` (if available) to determine which FR-006 scenario applies — if `data[1]` starts same calendar day as checkout → FR-006a: compute `checkout_time + (next_start - checkout_time) / 2`, schedule timer to `_transition_to_awaiting`; if no `data[1]` → FR-006b: compute `checkout_time + cleaning_window_hours`, schedule timer to `_transition_to_no_reservation`; if `data[1]` starts different day → FR-006c: compute midnight boundary `00:00` following checkout day, schedule timer to `_transition_to_no_reservation`; store computed `_transition_target_time` for attribute exposure
- [ ] T015 [US1] Implement FR-007 event identity protection in `custom_components/rental_control/sensors/checkinsensor.py`: when transitioning to `checked_out`, store `_checked_out_event_key = _event_key(summary, start)`; in `_handle_coordinator_update()`, when state is `checked_out` and the current event's key matches `_checked_out_event_key`, do NOT re-transition even if event end time changed; only allow new cycle when event key differs
- [ ] T046 [US1] Write unit test for event cancelled/removed in `tests/unit/test_checkin_sensor.py`: test that when a tracked event disappears from coordinator data (e.g., host cancels the reservation) while the sensor is in `awaiting_checkin` or `checked_in`, the sensor transitions to `no_reservation` or shifts tracking to the next available event on the next coordinator update cycle
- [ ] T048 [US1] Write unit test for FR-030 auto check-out rescheduling in `tests/unit/test_checkin_sensor.py`: starting from a `checked_in` state with an auto check-out timer scheduled at the original event `end`, simulate a coordinator update where the same event (same summary/start) has a different `end` time; assert that `_tracked_event_end` is updated, the existing `_unsub_timer` is cancelled, and a new `async_track_point_in_time()` callback is scheduled for the new `end` time; also verify that coordinator updates which do not change the event `end` do not cause the timer to be re-scheduled

**Checkpoint**: Sensor transitions through all four states based on event timing. HA events fire on check-in/check-out. Post-checkout linger works for all FR-006 scenarios. Event extensions after checkout are correctly ignored. Auto check-out timer rescheduling works when event end time changes (FR-030).

---

## Phase 4: User Story 2 — State Persistence Across Restarts (Priority: P1)

**Goal**: Sensor state persists across HA restarts via `RestoreEntity` and is validated against current time and calendar data on startup, auto-correcting stale state.

**Independent Test**: Set sensor to a known state, restart integration, verify state is restored and validated. If time has passed event boundaries, sensor auto-corrects.

### Tests for User Story 2 ⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T016 [US2] Write unit tests for state restoration in `tests/unit/test_checkin_sensor.py`: test that `async_added_to_hass` restores state, tracked event fields, checkin/checkout sources, checkout_time, transition_target_time, and checked_out_event_key from `RestoreEntity` last extra data; test that when no prior state exists sensor starts in `no_reservation`
- [X] T017 [US2] Write unit tests for stale state validation in `tests/unit/test_checkin_sensor.py`: test that restored `checked_in` state is kept when event is still active, test that restored `checked_in` transitions to `checked_out` when event has ended, test that restored `awaiting_checkin` transitions to `checked_in` when event start has passed (time-based mode), test that restored `checked_out` transitions to `awaiting_checkin` when a new event is relevant, and test that timers are re-scheduled after restore

### Implementation for User Story 2

- [X] T018 [US2] Implement `async_added_to_hass()` in `custom_components/rental_control/sensors/checkinsensor.py`: call `await super().async_added_to_hass()`, retrieve last extra data via `await self.async_get_last_extra_data()`, and if data exists (an `ExtraStoredData`/`SensorExtraStoredData` subclass instance) restore the sensor state and populate all related instance variables (`_tracked_event_summary`, `_tracked_event_start/end`, `_tracked_event_slot_name`, `_checkin_source`, `_checkout_source`, `_checkout_time`, `_transition_target_time`, `_checked_out_event_key`) from that object, parsing ISO 8601 strings back to datetime objects as needed
- [X] T019 [US2] Implement an `ExtraStoredData` (e.g., `SensorExtraStoredData` or a custom subclass) for `custom_components/rental_control/sensors/checkinsensor.py` that holds all persisted fields (state, tracked_event_summary, tracked_event_start/end as ISO 8601 strings, tracked_event_slot_name, checkin_source, checkout_source, checkout_time, transition_target_time, checked_out_event_key), and implement the `extra_restore_state_data` property to return an instance of this class for `RestoreEntity`/`RestoreSensor` storage instead of a raw dict
- [X] T020 [US2] Implement stale state validation in `custom_components/rental_control/sensors/checkinsensor.py`: at end of `async_added_to_hass()` after restoring state, compare restored state with current time (`dt_util.now()`) and current coordinator data — if `checked_in` but event ended → transition to `checked_out`; if `awaiting_checkin` but event start passed and time-based mode → transition to `checked_in`; if `checked_out` but linger period expired → transition to next appropriate state; re-schedule timers for any valid restored state with pending transitions

**Checkpoint**: Sensor correctly persists and restores state across HA restarts. Stale states are auto-corrected on startup.

---

## Phase 5: User Story 3 — Keymaster Unlock Detection (Priority: P2)

**Goal**: When keymaster is configured and monitoring toggle is enabled, the sensor transitions to `checked_in` upon detecting a matching keymaster unlock event instead of waiting for the event start time.

**Independent Test**: Configure keymaster-linked integration, enable monitoring toggle, simulate `keymaster_lock_state_changed` events with matching code slot numbers.

### Tests for User Story 3 ⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T021 [P] [US3] Write unit tests for `KeymasterMonitoringSwitch` in `tests/unit/test_switch.py`: test entity creation with correct unique_id and entity_id pattern, test `async_turn_on`/`async_turn_off` toggle state, test `RestoreEntity` restores last on/off state, test default state is `off`, test switch is NOT created when `coordinator.lockname` is empty (FR-026), and test switch IS created when `coordinator.lockname` is truthy
- [X] T022 [US3] Write unit tests for keymaster event handling in `tests/unit/test_checkin_sensor.py`: test that matching unlock event (correct lockname, state=`unlocked`, code_slot in managed range) transitions sensor from `awaiting_checkin` to `checked_in` with `source: keymaster`, test that `code_slot_num == 0` is ignored (FR-017), test that code_slot outside managed range `[start_slot, start_slot + max_events)` is ignored, test that unlock when sensor is already `checked_in` is ignored (FR-016), test that unlock when monitoring toggle is `off` is ignored, and test that `rental_control_checkin` event fires with `source: keymaster`

### Implementation for User Story 3

- [X] T023 [US3] Create `custom_components/rental_control/switch.py`: implement `async_setup_entry()` that conditionally creates switch entities when `coordinator.lockname` is truthy; implement `KeymasterMonitoringSwitch` class inheriting `SwitchEntity` + `RestoreEntity` with `async_turn_on()`/`async_turn_off()` methods, `unique_id` via `gen_uuid(f"{coordinator.unique_id} keymaster_monitoring")`, device_info linking to existing device, default `is_on = False`, and `async_added_to_hass()` that restores last known state and stores entity reference in `hass.data`
- [X] T024 [US3] Register keymaster event bus listener in `custom_components/rental_control/__init__.py`: in `async_setup_entry()`, when `coordinator.lockname` is truthy, register `hass.bus.async_listen("keymaster_lock_state_changed", callback)` where callback validates lockname matches `coordinator.lockname`, state is `"unlocked"`, `code_slot_num != 0`, and `code_slot_num` is in range `[coordinator.start_slot, coordinator.start_slot + coordinator.max_events)`; store unsubscribe function in `UNSUB_LISTENERS` for cleanup in `async_unload_entry()`
- [X] T025 [US3] Implement keymaster unlock handler in `custom_components/rental_control/sensors/checkinsensor.py`: add `async_handle_keymaster_unlock(code_slot_num)` method that verifies sensor is in `awaiting_checkin` and calls `_transition_to_checked_in(source="keymaster")`; switch on/off check is performed by the event bus listener before forwarding; `_transition_to_awaiting()` continues to schedule the time-based auto check-in timer even when keymaster monitoring is enabled (time-based fallback remains active)
- [X] T026 [US3] Wire keymaster event callback to sensor in `custom_components/rental_control/__init__.py`: in the event listener callback, after validating the unlock event and confirming the `KeymasterMonitoringSwitch` is `on` via stored entity reference, retrieve the `CheckinTrackingSensor` from `hass.data[DOMAIN][entry_id]` and call its `async_handle_keymaster_unlock()` method; store sensor reference in `hass.data[DOMAIN][entry_id]` during sensor platform setup
- [X] T047 [US3] Write unit test for keymaster monitoring toggle changed mid-event in `tests/unit/test_checkin_sensor.py`: test that toggling keymaster monitoring on while in `awaiting_checkin` starts listening for unlock events immediately, test that toggling off while in `awaiting_checkin` falls back to time-based auto check-in at event start, and test that toggling while already in `checked_in` has no effect on the current event

**Checkpoint**: Keymaster monitoring toggle appears when keymaster is configured. Matching unlock events trigger check-in. Non-matching events and manual/RF unlocks are correctly ignored. Time-based fallback works when monitoring is disabled.

---

## Phase 6: User Story 4 — Manual Guest Check-out (Priority: P2)

**Goal**: Property manager can invoke a checkout action on the sensor entity to trigger early departure, with guard conditions preventing invalid checkouts.

**Independent Test**: Place sensor in `checked_in` state, invoke `rental_control.checkout` action, verify transition to `checked_out` and event firing. Test guard rejections for wrong state, or when the current datetime is outside the active reservation window `[start_datetime, end_datetime)`.

### Tests for User Story 4 ⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [ ] T027 [US4] Write unit tests for checkout action in `tests/unit/test_checkin_sensor.py`: test successful checkout when `checked_in` and within the active reservation window fires `rental_control_checkout` with `source: manual` and transitions to `checked_out`; test that `ServiceValidationError` is raised when state is not `checked_in` (FR-019); test that `ServiceValidationError` is raised when the current datetime is outside the active reservation window `[start_datetime, end_datetime)` (FR-019), covering both before-start and at-or-after-end scenarios, and assert that the error message matches the active-window validation message defined in `contracts/checkout-service.md`; and test that post-checkout linger timing is computed correctly after manual checkout (using actual checkout time, not event end time)

### Implementation for User Story 4

- [ ] T028 [US4] Register checkout entity service in `custom_components/rental_control/sensor.py`: in `async_setup_entry()`, get current platform via `entity_platform.async_get_current_platform()` and call `platform.async_register_entity_service("checkout", {}, "async_checkout")` per contracts/checkout-service.md
- [ ] T029 [US4] Implement `async_checkout()` service method in `custom_components/rental_control/sensors/checkinsensor.py`: validate guard conditions — (1) sensor state is `checked_in` or raise `ServiceValidationError` with state in message, (2) current datetime is within the active reservation window (on or after event start datetime and strictly before event end datetime per FR-019) or raise error; if all guards pass, call `_transition_to_checked_out(source="manual")` which fires the checkout event and computes linger timing using `dt_util.now()` as the checkout time

**Checkpoint**: Checkout action succeeds when guards pass, fails with descriptive errors when guards fail. Manual checkout triggers correct linger timing from actual checkout time.

---

## Phase 7: User Story 5 — Early Check-out with Lock Code Expiry (Priority: P3)

**Goal**: When keymaster is configured and early expiry toggle is enabled, manual checkout also updates the keymaster slot's date range end to expire the lock code shortly after departure.

**Independent Test**: Configure keymaster, enable early expiry toggle, check in a guest, invoke checkout, verify keymaster slot end date updated to `min(now + 15min, original_event_end)`.

### Tests for User Story 5 ⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [ ] T030 [P] [US5] Write unit tests for `EarlyCheckoutExpirySwitch` in `tests/unit/test_switch.py`: test entity creation with correct unique_id and entity_id pattern, test `async_turn_on`/`async_turn_off` toggle, test `RestoreEntity` restore, test default `off`, test NOT created when `coordinator.lockname` is empty (FR-027), and test IS created when `coordinator.lockname` is truthy
- [ ] T031 [P] [US5] Write unit tests for early expiry helper in `tests/unit/test_util.py`: test `compute_early_expiry_time(now, original_end, grace_minutes=15)` returns `now + 15min` when more than 15min remain, returns `original_end` when less than 15min remain, and returns `original_end` when exactly 15min remain
- [ ] T032 [US5] Write unit tests for early expiry integration in `tests/unit/test_checkin_sensor.py`: test that manual checkout with expiry toggle `on` calls the keymaster slot update function with computed expiry time, test that manual checkout with expiry toggle `off` does NOT modify keymaster slots (FR-023), and test that manual checkout without keymaster configured does NOT attempt slot modification

### Implementation for User Story 5

- [ ] T033 [US5] Add `EarlyCheckoutExpirySwitch` to `custom_components/rental_control/switch.py`: implement class inheriting `SwitchEntity` + `RestoreEntity` with same pattern as `KeymasterMonitoringSwitch` — unique_id via `gen_uuid(f"{coordinator.unique_id} early_checkout_expiry")`, entity_id pattern `switch.rental_control_{calendar_name}_early_checkout_expiry`, default `is_on = False`, restore on startup; add to conditional creation block in `async_setup_entry()` alongside `KeymasterMonitoringSwitch`
- [ ] T034 [P] [US5] Add early expiry helper function to `custom_components/rental_control/util.py`: implement `compute_early_expiry_time(now: datetime, original_end: datetime, grace_minutes: int = 15) -> datetime` returning `min(now + timedelta(minutes=grace_minutes), original_end)`
- [ ] T035 [US5] Integrate early expiry into checkout flow in `custom_components/rental_control/sensors/checkinsensor.py`: in `async_checkout()`, after successful guard validation and before/after calling `_transition_to_checked_out("manual")`, check if `EarlyCheckoutExpirySwitch` is `on` (look up switch entity state); if enabled, look up the keymaster slot assigned to the current guest via the coordinator's slot mapping, compute expiry time using `compute_early_expiry_time()`, and update the slot's date range end using the existing `add_call()` / keymaster update pattern from `util.py` (per research RT-003 and plan D-004)

**Checkpoint**: Early expiry toggle controls whether lock codes expire on manual checkout. Grace period never exceeds original event end. No modification when toggle is off or keymaster absent.

---

## Phase 8: User Story 6 — Same-Day Turnover Handling (Priority: P3)

**Goal**: During same-day turnovers, the sensor correctly sequences the departing guest's checkout completion before shifting to the arriving guest's awaiting-checkin, and handles all three post-checkout linger scenarios accurately.

**Independent Test**: Configure two events (event 0 ends today, event 1 starts today). Verify sensor tracks event 0 through checkout, lingers for half-gap, then shifts to event 1's awaiting_checkin.

### Tests for User Story 6 ⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [ ] T036 [US6] Write integration tests for same-day turnover (FR-006a) in `tests/integration/test_checkin_tracking.py`: test full lifecycle with same-day turnover — event 0 checked_out at end time, sensor lingers for half the gap between checkout and event 1 start, then transitions to `awaiting_checkin` for event 1; verify sensor does NOT prematurely switch to event 1 while event 0 is still active (FR-029)
- [ ] T037 [US6] Write integration tests for different-day (FR-006c) and no-follow-on (FR-006b) in `tests/integration/test_checkin_tracking.py`: test different-day scenario — checkout → linger until midnight → `no_reservation` → `awaiting_checkin` at 00:00 on next event's start day; test no-follow-on scenario — checkout → linger for cleaning window hours → `no_reservation`

### Implementation for User Story 6

- [ ] T038 [US6] Validate and refine next-event evaluation in `custom_components/rental_control/sensors/checkinsensor.py`: ensure `_handle_coordinator_update()` correctly handles event sequencing during turnovers — when `checked_out` for event 0, do NOT shift tracked event to event 1 until the linger period completes; when `awaiting_checkin` for event 1, correctly associate with event 1's data; when event 0 is still active (`checked_in`), continue tracking event 0 even if event 1 is also visible in coordinator data
- [ ] T039 [US6] Implement FR-006c midnight-to-awaiting transition in `custom_components/rental_control/sensors/checkinsensor.py`: after the midnight boundary `_transition_to_no_reservation()` fires for FR-006c, schedule a follow-up timer at `00:00` on the next event's start day to transition from `no_reservation` to `awaiting_checkin`; store the next event's start day in transition metadata so the timer can be re-scheduled on restore

**Checkpoint**: All three FR-006 post-checkout scenarios work correctly end-to-end. Same-day turnovers sequence properly. Different-day turnovers use midnight boundary. No-follow-on uses cleaning window.

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: Config flow changes, translations, full lifecycle integration tests, and validation.

- [ ] T040 [P] Add `cleaning_window` option to options flow in `custom_components/rental_control/config_flow.py`: add `vol.Optional(CONF_CLEANING_WINDOW, default=DEFAULT_CLEANING_WINDOW): vol.All(vol.Coerce(float), vol.Range(min=0.5, max=48.0))` to the options flow schema; ensure the value is accessible via `config_entry.options.get(CONF_CLEANING_WINDOW, DEFAULT_CLEANING_WINDOW)` in the sensor
- [ ] T041 [P] Add translations for new entities and config options to `custom_components/rental_control/strings.json`: add `cleaning_window` label and description under options flow data schema, add entity translations for `sensor.checkin` (with state translations for all four states), `switch.keymaster_monitoring`, and `switch.early_checkout_expiry`; add service description for `checkout` action
- [ ] T042 [P] Add English translations to `custom_components/rental_control/translations/en.json`: mirror all new entries from `strings.json` into the English translation file with user-friendly labels — "Cleaning Window (hours)" for the config option, "Check-in Tracking" for the sensor, "Keymaster Monitoring" and "Early Checkout Lock Expiry" for switches
- [ ] T043 Write full lifecycle integration test in `tests/integration/test_checkin_tracking.py`: test complete flow from integration setup → sensor created → coordinator update with event → `awaiting_checkin` → auto check-in at event start → `checked_in` → auto check-out at event end → `checked_out` → linger → `no_reservation`; verify all HA events fired with correct payloads; verify sensor attributes at each state; test with keymaster configured and without
- [ ] T044 Run pre-commit validation: execute `pre-commit run --all-files` and fix any issues from ruff (lint + format), mypy (type checking), interrogate (docstring coverage), reuse-tool (SPDX headers on all new files), and gitlint
- [ ] T045 Run full test suite with coverage per quickstart.md: execute `uv run pytest tests/ --cov=custom_components/rental_control --cov-report=term-missing -v` and verify all new and existing tests pass with no regressions; execute `uv run ruff check` and `uv run mypy` independently to confirm clean output

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 completion — BLOCKS all user stories
- **US1 (Phase 3)**: Depends on Phase 2 — core state machine
- **US2 (Phase 4)**: Depends on Phase 3 — persistence requires working state machine
- **US3 (Phase 5)**: Depends on Phase 3 — keymaster detection requires working state machine
- **US4 (Phase 6)**: Depends on Phase 3 — checkout action requires working state machine
- **US5 (Phase 7)**: Depends on Phase 5 (switch entities) AND Phase 6 (checkout action)
- **US6 (Phase 8)**: Depends on Phase 3 — turnover handling is state machine extension
- **Polish (Phase 9)**: Depends on Phases 3–8 being complete

### User Story Dependencies

```text
Phase 1 (Setup)
    │
    ▼
Phase 2 (Foundational)
    │
    ▼
Phase 3 (US1: Core Tracking) ─── MVP CHECKPOINT
    │
    ├──────────────┬──────────────┐
    ▼              ▼              ▼
Phase 4 (US2)  Phase 5 (US3)  Phase 6 (US4)   Phase 8 (US6)
(Persistence)  (Keymaster)    (Checkout)       (Turnovers)
    │              │              │
    │              └──────┬───────┘
    │                     ▼
    │              Phase 7 (US5: Early Expiry)
    │                     │
    └──────────┬──────────┘
               ▼
        Phase 9 (Polish)
```

### Within Each User Story

1. Tests MUST be written and FAIL before implementation
2. Entity skeletons before business logic
3. Core transitions before edge case handling
4. Story complete and testable before moving to next priority

### Parallel Opportunities

- **Phase 1**: T002 and T003 can run in parallel with T001 (different files)
- **After Phase 3**: US2 (Phase 4), US3 (Phase 5), US4 (Phase 6), and US6 (Phase 8) can start in parallel — they touch different capabilities and mostly different files
- **Phase 5**: T021 (test_switch.py) and T022 (test_checkin_sensor.py) can run in parallel (different files)
- **Phase 7**: T030 (test_switch.py), T031 (test_util.py), and T034 (util.py) can run in parallel (different files)
- **Phase 9**: T040, T041, T042 can run in parallel (different files)

---

## Parallel Example: User Story 1

```bash
# Tests can be written in parallel (same file but independent test classes):
Task T007: "State machine transition tests in tests/unit/test_checkin_sensor.py"
Task T008: "Event identity tests in tests/unit/test_checkin_sensor.py"
Task T009: "Event bus firing tests in tests/unit/test_checkin_sensor.py"

# After tests, implementation builds sequentially within checkinsensor.py:
Task T010: "Coordinator update handler" → Task T011: "Timer transitions"
    → Task T012: "State transition methods" → Task T013: "Event bus firing"
    → Task T014: "Post-checkout linger" → Task T015: "FR-007 identity protection"
```

## Parallel Example: After US1 Complete

```bash
# These four phases can start simultaneously (different capabilities):
Phase 4 (US2): State persistence in sensors/checkinsensor.py
Phase 5 (US3): Switch entities in switch.py + listener in __init__.py
Phase 6 (US4): Checkout service in sensor.py + handler in checkinsensor.py
Phase 8 (US6): Turnover tests in test_checkin_tracking.py + refinements
```

---

## Implementation Strategy

### MVP First (User Stories 1 + 2 Only)

1. Complete Phase 1: Setup (constants, fixtures)
2. Complete Phase 2: Foundational (sensor skeleton, platform wiring)
3. Complete Phase 3: US1 — Core Occupancy Tracking
4. **STOP and VALIDATE**: Sensor transitions through all states, events fire
5. Complete Phase 4: US2 — State Persistence
6. **STOP and VALIDATE**: Restart HA, verify state restored correctly
7. Deploy/demo — basic occupancy tracking is fully operational

### Incremental Delivery

1. Setup + Foundational → Skeleton loads in HA
2. US1 (Core Tracking) → Sensor works end-to-end (MVP!)
3. US2 (Persistence) → Survives restarts (production-ready MVP)
4. US3 (Keymaster) + US4 (Checkout) → Enhanced detection + manual control
5. US5 (Early Expiry) → Security enhancement for keymaster users
6. US6 (Turnovers) → Edge case correctness
7. Polish → Config flow, translations, full validation

### Single Developer Strategy

1. Complete Setup + Foundational together
2. US1 → Test independently → Commit
3. US2 → Test independently → Commit
4. US3 → Test independently → Commit
5. US4 → Test independently → Commit
6. US5 → Test independently → Commit
7. US6 → Test independently → Commit
8. Polish → Full validation → Final commit

---

## Notes

- [P] tasks = different files, no dependencies on incomplete tasks
- [Story] label maps task to specific user story for traceability
- Each user story is independently completable and testable
- Commit after each task or logical group
- All new files MUST include SPDX headers (Constitution Principle III)
- All commits MUST use `git commit -s` for DCO sign-off (Constitution Principle V)
- Pre-commit hooks (ruff, mypy, interrogate, reuse-tool) must pass before push
- The sensor file `sensors/checkinsensor.py` is the primary implementation target — most user stories add methods to this class
- Switch entities only exist when keymaster is configured — test both scenarios
