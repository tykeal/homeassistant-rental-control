<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Feature Specification: Decompose Calendar Sensor

**Feature Branch**: `019-decompose-calsensor`
**Created**: 2026-06-29
**Status**: Draft
**Input**: User description: "Decompose
`custom_components/rental_control/sensors/calsensor.py` for GitHub issue #576.
This is a behavior-preserving code-health refactor of the oversized Rental
Control calendar sensor. Extract attribute building, state mapping, and
slot-assignment preparation helpers without changing Home Assistant-visible
sensor behavior or the compatibility surface imported from `calsensor.py`."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Preserve Calendar Sensor State (Priority: P1)

As a property manager, I want every upcoming-reservation sensor to report the
same state and attributes after decomposition, so dashboards, automations, and
lock-code workflows continue to see identical reservation information.

**Why this priority**: `RentalControlCalSensor` exposes reservation summary,
start/end times, ETA values, guest-derived attributes, slot names, slot numbers,
and slot codes. Drift in these values can break automations or show incorrect
access details.

**Independent Test**: Can be fully tested by running existing calendar-sensor
unit tests unchanged and by comparing state strings, `extra_state_attributes`,
ETA values, UID normalization, generated door codes, slot names, slot numbers,
and no-reservation resets for identical coordinator data before and after
decomposition.

**Acceptance Scenarios**:

1. **Given** a coordinator update contains an event for this sensor index,
   **When** the decomposed sensor handles the update, **Then** the state string,
   base event attributes, parsed guest attributes, ETA fields, slot name,
   slot number, and slot code match the current implementation exactly.
2. **Given** the event start time is in the past, **When** ETA fields are built,
   **Then** `eta_days`, `eta_hours`, and `eta_minutes` remain `None` exactly as
   today.
3. **Given** no event exists for this sensor index, **When** the sensor updates,
   **Then** the state and attributes reset to the same no-reservation values,
   including configured event-prefix handling and clearing of slot attributes.
4. **Given** coordinator code-generator settings change between refreshes,
   **When** the sensor handles the next update, **Then** it rereads those settings
   and produces the same date-based, last-four, or static-random code behavior.
5. **Given** `async_added_to_hass` registers after the first coordinator refresh,
   **When** coordinator data is already available and successful, **Then** the
   existing immediate update behavior remains unchanged.

---

### User Story 2 - Preserve Read-Only Slot Behavior (Priority: P1)

As an existing Rental Control user, I want the calendar sensor to remain a
read-only view of reconciliation state, so decomposition cannot reintroduce slot
mutation, background task scheduling, or direct Keymaster service calls.

**Why this priority**: The current sensor no longer owns slot assignment. It
reads `get_slot_assignment` and `get_slot_code` from coordinator reconciliation
state and falls back to local code generation only when reconciliation has no
code. Reintroducing mutation paths could program or clear physical lock slots
from the wrong layer.

**Independent Test**: Can be fully tested by existing read-only sensor tests
unchanged, including assertions that no reservation, set-code, clear-code,
update-times, or async-task scheduling path is called from coordinator updates.

**Acceptance Scenarios**:

1. **Given** reconciliation state is available for a reservation, **When** the
   sensor updates, **Then** it reads the same identity key, slot assignment, and
   slot code without calling any slot-reservation or Keymaster service helper.
2. **Given** no lock or event-overrides object is configured, **When** the sensor
   updates, **Then** it skips reconciliation lookup and produces the same local
   generated code behavior.
3. **Given** reconciliation has no code for an event, **When** the sensor updates,
   **Then** it falls back to `_generate_door_code` exactly as today.
4. **Given** the deprecated slot-assignment shim is invoked by compatibility
   callers or tests, **When** it runs after decomposition, **Then** it remains a
   harmless no-op and does not call `async_reserve_or_get_slot` or schedule work.

---

### User Story 3 - Preserve calsensor Compatibility Surface (Priority: P1)

As a Rental Control maintainer, I want the current calendar-sensor module and
entity surface to remain importable and callable, so this refactor can be
reviewed as a behavior-preserving decomposition rather than a coordinated caller
rewrite.

**Why this priority**: `sensor.py` imports and constructs
`RentalControlCalSensor`; sensor tests instantiate it directly, inspect its
properties, exercise current helper seams, and patch the module-level slot-name
and fingerprint helpers.

**Independent Test**: Can be fully tested by running existing `test_sensors.py`
and reconciliation import tests unchanged and by verifying production setup
continues to import `RentalControlCalSensor` from
`custom_components.rental_control.sensors.calsensor` with the same constructor
arguments.

**Acceptance Scenarios**:

1. **Given** `sensor.py` imports `RentalControlCalSensor`, **When** sensor setup
   creates event entities, **Then** the class remains importable from
   `calsensor.py` and accepts the same `hass`, `coordinator`, `sensor_name`, and
   `event_number` construction pattern.
2. **Given** tests access calendar-sensor properties, **When** the decomposed
   entity is instantiated, **Then** `name`, `unique_id`, `state`, `available`,
   `extra_state_attributes`, `icon`, `entity_category`, and `device_info` remain
   behavior-compatible.
3. **Given** tests exercise current helper seams, **When** the decomposed module
   is loaded, **Then** existing direct calls to `_handle_coordinator_update`,
   `_generate_door_code`, `_extract_email`, `_extract_phone_number`,
   `_extract_num_guests`, `_extract_last_four`, `_extract_url`,
   `_extract_booking_id`, `_extract_dynamic_attributes`, `async_added_to_hass`,
   and `_async_handle_slot_assignment` continue to produce the same observable
   behavior.
4. **Given** tests patch module-level `get_slot_name` and
   `make_reservation_fingerprint`, **When** the sensor update path runs, **Then**
   those patch seams remain effective without import rewrites.
5. **Given** compatibility tests import the calsensor module, **When** the module
   is decomposed, **Then** `custom_components.rental_control.sensors.calsensor`
   still imports successfully without requiring callers to know helper locations.

---

### User Story 4 - Improve Maintainability Under Aislop Limits (Priority: P2)

As a maintainer, I want the calendar-sensor implementation split into focused,
independently testable concerns, so future reservation-display fixes can target
attribute building, state mapping, or slot-read preparation without navigating an
oversized entity file.

**Why this priority**: Issue #576 identifies `calsensor.py` as over the active
400-line file threshold, with `_handle_coordinator_update` over the 80-line
function threshold and `_async_handle_slot_assignment` over the parameter-count
threshold. The file has no `aislop-ignore-file` directive, so the findings should
be resolved rather than suppressed.

**Independent Test**: Can be fully tested by measuring the decomposed
calendar-sensor feature area against active complexity thresholds while existing
behavior tests continue to pass unchanged.

**Acceptance Scenarios**:

1. **Given** the decomposition is complete, **When** complexity checks run,
   **Then** calendar-sensor-related files are below 400 lines, project-owned
   functions are below 80 lines, and project-owned parameter lists have no more
   than 6 parameters.
2. **Given** the entity class remains Home Assistant-facing, **When** the code is
   inspected, **Then** `RentalControlCalSensor` is a shell around focused
   behavior-preserving helpers rather than the owner of all attribute, state, and
   slot-read preparation logic.
3. **Given** no complexity directive exists on `calsensor.py` today, **When** the
   implementation resolves the findings, **Then** no new Aislop ignore or
   suppression directive is added to hide file-size, function-length, or
   parameter-count complexity.
4. **Given** planning and implementation decide exact helper boundaries, **When**
   work is scoped, **Then** the split is by coherent concern and does not depend
   on exact module names prescribed by this specification.

---

### Edge Cases

- What happens when coordinator updates are unsuccessful? The sensor still writes
  current Home Assistant state without rebuilding event attributes.
- What happens when a calendar event lacks a UID, has a whitespace-only UID, or
  has no description? UID normalization, parsed attributes, and code-generator
  fallback behavior remain exact.
- What happens when reservation descriptions contain Airbnb, VRBO, Guesty,
  Booking.com, phone-last-four, guest-count, URL, booking ID, or unknown
  `Field: Value` lines? The same dedicated and dynamic attributes are exposed.
- What happens when reconciliation is absent, not ready, has no assignment, or
  has no slot code? The current lookup skipping, `None` slot number, and generated
  code fallback behavior remains unchanged.
- What happens when an event list shrinks or becomes empty? The sensor resets the
  same event attributes and parsed attributes, including `slot_number` and
  `slot_code`, to no-reservation state.
- What happens when visible or hidden tests patch calsensor module seams? The
  current import and monkeypatch boundaries remain effective.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The decomposition MUST preserve all Home Assistant observable
  behavior of `RentalControlCalSensor`, including entity naming, availability,
  unique IDs, diagnostic category, icon, device info, state strings, state writes,
  and `extra_state_attributes` contents.
- **FR-002**: Existing calsensor-related tests MUST pass unchanged after the
  implementation stage; new tests MUST verify behavior parity or focused
  extracted behavior rather than introduce new runtime behavior.
- **FR-003**: The public production import surface consumed by `sensor.py` MUST
  remain source-compatible: `RentalControlCalSensor` importable from
  `custom_components.rental_control.sensors.calsensor` and constructible with the
  existing four-argument pattern used during sensor setup.
- **FR-004**: The compatibility surface consumed by current tests MUST remain
  behavior-compatible, including `RentalControlCalSensor`, `get_slot_name`,
  `make_reservation_fingerprint`, `_handle_coordinator_update`,
  `_generate_door_code`, all current description extractor helpers,
  `async_added_to_hass`, `_async_handle_slot_assignment`, `_event_attributes`,
  `_parsed_attributes`, `_code_generator`, `_code_length`, and `_event_number`.
- **FR-005**: `_handle_coordinator_update` behavior MUST remain equivalent for
  unsuccessful updates, event selection by event number, code-generator setting
  refresh, state formatting, event attribute assignment, UID normalization, ETA
  calculation, slot-name derivation, reconciliation lookup, generated-code
  fallback, parsed-description attributes, dynamic attributes, no-reservation
  reset, and final Home Assistant state write.
- **FR-006**: ETA calculation MUST preserve current semantics exactly: future
  start times produce the same day, hour, and minute values based on the event
  timezone; past starts produce `None` for all ETA fields.
- **FR-007**: Door-code generation MUST preserve current date-based, last-four,
  static-random UID-seeded, static-random description-fallback, empty-UID, and
  no-description fallback behavior, including deterministic random seeding and
  code-length handling.
- **FR-008**: Description parsing MUST preserve current dedicated extraction for
  email, phone number, number of guests, last four digits, reservation URL,
  booking ID, and dynamic unrecognized fields, including known-field skipping and
  slugification.
- **FR-009**: Reconciliation integration MUST remain read-only from the sensor:
  the sensor may read coordinator-owned slot assignment and slot code for the same
  reservation fingerprint, but MUST NOT reserve slots, mutate event overrides,
  call Keymaster set/clear/update-time helpers, or schedule async tasks from the
  coordinator update path.
- **FR-010**: Integration with `event_overrides` and reconciliation MUST remain
  compatible, including the current use of coordinator `entry_id`, slot name,
  event start, and event end to compute the reservation fingerprint before
  reading coordinator assignment and code state.
- **FR-011**: The `_async_handle_slot_assignment` no-op compatibility behavior
  MUST remain harmless and unscheduled, while its project-owned parameter shape is
  brought under the active limit using a grouped context value or equivalent
  approach that does not change observable behavior.
- **FR-012**: The completed decomposition MUST keep calendar-sensor-related files
  below 400 lines, project-owned functions below 80 lines, and project-owned
  parameter lists at no more than 6 parameters unless an external framework
  signature requires otherwise.
- **FR-013**: The implementation MUST NOT add any new Aislop ignore or
  suppression directive, including `aislop-ignore`, `aislop-ignore-file`, or
  equivalent directives in `calsensor.py` or replacement modules, to suppress
  file-size, function-length, or parameter-count findings.
- **FR-014**: Decomposition MUST NOT introduce blocking I/O, additional
  coordinator refreshes, additional Home Assistant state writes, new
  reconciliation launches, Keymaster service calls, new async tasks, new sensors,
  new services, or user-visible delays compared with the current implementation.
- **FR-015**: Planning and implementation documentation MUST state that this is a
  behavior-preserving refactor and MUST NOT define new lock-code business rules,
  reservation parsing semantics, slot-assignment behavior, sensors,
  configuration options, or changed public caller behavior.

### Key Entities

- **Calendar Sensor Entity**: The Home Assistant-facing `RentalControlCalSensor`
  that represents the Nth upcoming reservation and remains the compatibility
  shell imported by sensor setup and tests.
- **Event Attribute Snapshot**: The summary, description, location, start, end,
  UID, ETA, slot name, slot code, and slot number values exposed through
  `extra_state_attributes` for each event or no-reservation state.
- **Parsed Reservation Attributes**: Guest-facing attributes derived from event
  descriptions, including last four digits, guest count, email, phone,
  reservation URL, booking ID, and dynamic fields.
- **Door Code Decision**: The behavior-preserving generated-code result selected
  from last-four, static-random, or date-based inputs when reconciliation does not
  provide a slot code.
- **Slot Read Context**: The reservation identity data used to read coordinator
  reconciliation state without mutating slots: entry ID, slot name, start, end,
  and resulting fingerprint.
- **Slot Assignment Compatibility Context**: A grouped value or equivalent
  compatibility mechanism used to keep the deprecated no-op assignment path below
  parameter thresholds without changing its side-effect-free behavior.

## Assumptions

- This specification covers issue #576's spec stage only; planning and
  implementation stages will decide exact helper boundaries, module layout,
  dataclass or context shape, and compatibility mechanics.
- Issue #576 recorded `calsensor.py` at 607 lines in an earlier snapshot;
  the live source read for this specification is a 537-line
  `custom_components/rental_control/sensors/calsensor.py`, still above the
  active 400-line threshold, with no `aislop-ignore-file` directive.
- The active findings are one file-size finding over the 400-line threshold, one
  long `_handle_coordinator_update` function over the 80-line threshold, and one
  over-parameter `_async_handle_slot_assignment` compatibility shim with seven
  keyword-only inputs plus `self`.
- The existing source and tests are the behavior source of truth unless a later
  accepted issue explicitly changes calendar-sensor behavior.
- Current production consumption is limited to `sensor.py` importing and
  constructing `RentalControlCalSensor`; current visible tests also directly
  exercise entity properties, private helper seams, module importability, and
  module-level monkeypatch seams.
- Runtime performance expectations are parity with the current implementation in
  normal Home Assistant operation, not a new user-visible performance feature.

## Non-Goals

- Changing Home Assistant-visible calendar sensor state, attributes, availability,
  unique IDs, device metadata, or immediate update timing.
- Changing door-code generation, description parsing, ETA calculation, slot-name
  derivation, reservation fingerprinting, or no-reservation reset semantics.
- Reintroducing sensor-owned slot mutation, Keymaster service calls, event
  override writes, background task scheduling, or reconciliation authority.
- Adding new features, services, sensors, automations, diagnostics fields,
  configuration options, Store authority, or recovery workflows.
- Changing the public `RentalControlCalSensor` import and construction surface or
  the calsensor module seams consumed by current production callers and tests.
- Prescribing exact file names, helper module names, class names, dataclass field
  names, or helper signatures for the plan and implementation stages.
- Adding any Aislop ignore or suppression directive for calendar-sensor
  complexity findings.
- Closing issue #576 in this specification PR; later implementation work owns the
  runtime refactor.

## Constraints

- No behavior observable by Home Assistant users, dashboards, automations,
  services, logs relied on by tests, physical Keymaster state, reconciliation
  state, or existing tests may change as part of this refactor.
- `RentalControlCalSensor` MUST remain the Home Assistant-facing entity class and
  compatibility shell imported from `calsensor.py`.
- The sensor MUST remain read-only with respect to event overrides,
  reconciliation, and Keymaster service helpers.
- Existing `sensor.py` construction and current test import, direct-call, and
  monkeypatch boundaries MUST remain compatible.
- The final implementation MUST satisfy the active file-size, function-length,
  and parameter-count thresholds without adding suppressing directives.
- This specification stage is documentation-only and MUST NOT include production
  code changes.

## Security Considerations

- Calendar sensor attributes include physical access information such as slot
  names, slot numbers, and slot codes. Behavior drift can expose wrong access
  details or mislead automations that coordinate guest entry.
- The sensor's read-only boundary is safety-critical: slot assignment and
  Keymaster mutation must remain owned by coordinator reconciliation and
  event-overrides logic, not by entity update rendering.
- Logs, attributes, and helper boundaries must continue to expose no more raw PIN
  information than existing Rental Control behavior already exposes.
- Behavior parity for generated codes, reconciliation lookups, and no-op
  assignment compatibility must be verified before any complexity improvement is
  considered successful.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of existing calsensor-related unit and integration tests pass
  unchanged after the implementation stage completes, including `test_sensors.py`
  and reconciliation module import coverage.
- **SC-002**: In 100% of covered event-update scenarios, the decomposed sensor
  produces identical state strings, base event attributes, parsed attributes, ETA
  values, UID values, slot names, slot numbers, and slot codes as the current
  implementation.
- **SC-003**: In 100% of covered no-event and unsuccessful-update scenarios,
  state writes, no-reservation summaries, cleared attributes, and parsed-attribute
  resets match the current implementation.
- **SC-004**: In 100% of covered code-generation scenarios, date-based,
  last-four, static-random UID, static-random description fallback, empty UID,
  and no-description fallback codes match current outputs for identical inputs.
- **SC-005**: All production modules and tests that currently import, construct,
  patch, or directly call calsensor names continue to do so without behavior
  changes or behavior-assertion rewrites.
- **SC-006**: The deprecated slot-assignment compatibility path remains a no-op
  and no coordinator update path calls slot-reservation, set-code, clear-code,
  update-times, or async-task scheduling helpers.
- **SC-007**: The decomposed calendar-sensor feature area contains no files of
  400 lines or more, no project-owned functions of 80 lines or more, and no
  project-owned parameter lists over 6 parameters.
- **SC-008**: Active complexity checks pass without adding any calendar-sensor
  Aislop ignore or suppression directive for file size, function length, or
  parameter count.
- **SC-009**: Normal sensor update processing performs no additional Home
  Assistant state writes, coordinator refreshes, reconciliation launches,
  blocking I/O, Keymaster service calls, async task scheduling, or user-visible
  delays compared with the current implementation.
- **SC-010**: No production-code changes are included in this specification PR;
  the PR contains only the feature specification for the #576 decomposition
  pipeline.
