<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Feature Specification: Decompose Coordinator

**Feature Branch**: `016-decompose-coordinator`
**Created**: 2026-06-27
**Status**: Draft
**Input**: User description: "Decompose
`custom_components/rental_control/coordinator.py` for GitHub issue #574. This
is a behavior-preserving code-health refactor of the integration's largest and
most central Home Assistant DataUpdateCoordinator module. Split calendar feed
parsing, reservation building, physical-slot observation, Keymaster override
setup/adoption, check-in protection, and diagnostics/event-recording concerns
from coordinator orchestration without changing runtime behavior."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Preserve Refresh-Cycle Behavior (Priority: P1)

As a property manager, I want every Rental Control calendar refresh to produce
the same reservations, observed slots, desired plan, lock-code operations, and
Home Assistant state after decomposition, so upgrades cannot change tenant
access or automation behavior.

**Why this priority**: The coordinator drives the integration hot path. It
fetches and parses calendar data, observes physical Keymaster slot state, builds
reservations, computes the desired reconciliation plan, applies slot operations,
updates Store metadata, and exposes calendar data to sensors. Any behavior drift
could affect physical access.

**Independent Test**: Can be fully tested by running the existing coordinator,
refresh-cycle, slot-reconciliation, event-override, calendar, sensor, and
check-in tests unchanged and by comparing refresh outputs and side effects for
identical calendar, Store, and Keymaster state before and after decomposition.

**Acceptance Scenarios**:

1. **Given** a successful calendar fetch with Keymaster enabled, **When** the
   coordinator refreshes, **Then** parsed events, current event selection,
   managed-slot observations, reservations, desired plan, Store update, and
   applied Keymaster actions match the current implementation.
2. **Given** a calendar fetch or parse failure after prior data exists, **When**
   the coordinator refreshes, **Then** cached data, miss counting, warnings, and
   subsequent reconciliation behavior remain unchanged.
3. **Given** an empty fresh feed after prior non-empty data, **When** miss
   tolerance has not been exceeded, **Then** previous calendar data is preserved
   and the same miss counter behavior is observed.
4. **Given** Keymaster is not configured, **When** the coordinator refreshes,
   **Then** the same calendar-only update path runs without slot observation,
   reconciliation, or override side effects.
5. **Given** child lock discovery changes during a refresh, **When** the cycle
   completes, **Then** monitored locknames and logging behavior match the
   current implementation.

---

### User Story 2 - Preserve Calendar Parsing and Reservations (Priority: P1)

As an existing Rental Control user, I want iCal events and reservation objects
to be interpreted exactly as before, so guest names, access windows, generated
codes, manual overrides, and ignored events do not change after the refactor.

**Why this priority**: Calendar interpretation feeds every downstream lock-code
decision. The oversized `_ical_parser`, `_build_reservations`, and
`_build_ghost_reservations` functions contain behavior for ignored events,
timezone conversion, Honor Event Times, manual override fallback, buffers,
fingerprints, ghost reservations, and manual PIN preservation.

**Independent Test**: Can be fully tested by replaying existing iCal parsing,
timezone, event override, duplicate-name, ghost-reservation, buffer, and manual
PIN scenarios with unchanged tests and identical `CalendarEvent` and
reservation outputs.

**Acceptance Scenarios**:

1. **Given** the same iCal feed, configured timezone, event prefix, ignored-event
   setting, Honor Event Times setting, and override state, **When** parsing
   completes, **Then** the sorted `CalendarEvent` list is identical to the
   current parser output.
2. **Given** date-only, timezone-aware, description-time, Smoobu extra,
   RRULE-bearing, blocked, not-available, stale, far-future, and missing-UID
   feed entries, **When** parsing runs, **Then** inclusion, exclusion, logging,
   timezone conversion, UID normalization, and summaries match current behavior.
3. **Given** reservations with duplicate names, shifted dates, same starts,
   manual PINs, configured prefixes, trimming, booking aliases, UID aliases, and
   buffer windows, **When** reservations are built, **Then** identity keys,
   display names, generated or observed codes, aliases, lookup keys, and manual
   code preservation match current behavior.
4. **Given** assigned reservations disappear from the feed, **When** ghost
   reservations are built, **Then** missing-count increments, pending-set to
   pending-clear transitions, skipped invalid ghosts, fingerprint history, and
   empty raw PIN handling remain unchanged.

---

### User Story 3 - Preserve Keymaster Observation and Overrides (Priority: P1)

As a maintainer of a lock-code integration, I want physical Keymaster state
observation, first-load override setup, slot adoption, and service helper
coordination to behave exactly as before, so decomposition cannot wipe codes,
reuse unsafe slots, or change manual override semantics.

**Why this priority**: The coordinator reads physical slots and calls Keymaster
service helpers that control property access. The current implementation also
contains safety behavior for unreadable states, partially reset slots,
placeholder adoption, phantom slots, cache-only Store metadata, and buffered
manual override times.

**Independent Test**: Can be fully tested by running existing Keymaster setup,
adoption, refresh-cycle, buffer-update, clear-failure, deleted-Store,
unavailable-slot, diagnostics, and event-override tests unchanged.

**Acceptance Scenarios**:

1. **Given** populated, empty, unreadable, unavailable, partially reset, phantom,
   and unnamed coded Keymaster slots, **When** overrides are bootstrapped or slots
   are observed, **Then** slot classification, placeholder names, forced resets,
   skipped unreadable states, and recorded actual-state diagnostics match the
   current implementation.
2. **Given** an empty or deleted Store, **When** adoption and refresh run,
   **Then** existing code-bearing slots are adopted without storing raw PINs or
   modifying physical state beyond the current behavior.
3. **Given** buffer configuration changes for assigned slots, **When** override
   times are updated, **Then** old-buffer reversal, new-buffer application,
   service calls, exception handling, and override-cache advancement match the
   current implementation.
4. **Given** reconciliation plans require set, update, clear, retry, no-op, or
   blocked actions, **When** the coordinator applies the plan through
   `EventOverrides` and service helpers, **Then** action ordering, Store sync,
   retry state, actual-state reads, and diagnostics remain unchanged.

---

### User Story 4 - Preserve Public Coordinator Surface (Priority: P1)

As a Rental Control maintainer, I want the Home Assistant-facing coordinator
class and the methods and attributes used by platform setup, entities,
listeners, utilities, and tests to remain available, so decomposition can be
reviewed as a behavior-preserving refactor rather than a public API redesign.

**Why this priority**: Production modules import `RentalControlCoordinator`,
store it in `hass.data`, subclass `CoordinatorEntity` with it, call its public
methods, and read its attributes during setup, refresh, entity rendering,
listener filtering, and service helper execution.

**Independent Test**: Can be fully tested by running existing tests unchanged
and by verifying all production import sites and coordinator attribute/method
uses continue to load and execute with the same call semantics and observable
results.

**Acceptance Scenarios**:

1. **Given** `custom_components/rental_control/__init__.py` imports and
   instantiates `RentalControlCoordinator`, **When** setup, update-listener,
   startup readability refresh, and listener registration paths run, **Then**
   the class name, construction behavior, stored coordinator object, lifecycle
   methods, and consumed attributes remain compatible.
2. **Given** calendar, event sensors, check-in tracking sensor, switches,
   listeners, utilities, and event override helpers access coordinator data,
   **When** those modules run, **Then** every consumed public method and
   attribute remains available with unchanged behavior.
3. **Given** tests import the coordinator module and construct or mock the
   coordinator, **When** the test suite runs unchanged, **Then** imports, direct
   attribute access, and method call expectations continue to pass.
4. **Given** the consumed `update_event_overrides` coordinator entry point is
   reduced to satisfy the parameter-count threshold, **When** current service
   helper and bootstrap behavior is exercised, **Then** callers observe the same
   override update, optional refresh request, and side effects as before.

---

### User Story 5 - Improve Maintainability Under Aislop Limits (Priority: P2)

As a maintainer, I want the oversized coordinator split into focused,
independently testable units, so future fixes can target parsing, reservation
building, slot observation, override setup, check-in protection, and diagnostics
without navigating one 2,948-line orchestration file.

**Why this priority**: Issue #574 identifies `coordinator.py` as the largest and
most central file in the integration. Its current complexity is hidden by a
file-level `aislop-ignore-file` directive for file size and function length,
which must be removed after behavior-preserving decomposition.

**Independent Test**: Can be fully tested by measuring the decomposed feature
area against active complexity thresholds and by adding focused tests for
extracted concerns while existing end-to-end coordinator behavior tests continue
to pass unchanged.

**Acceptance Scenarios**:

1. **Given** the decomposition is complete, **When** complexity checks run,
   **Then** coordinator-related files are below 400 lines, project-owned
   functions are below 80 lines, and project-owned parameter lists have no more
   than 6 parameters.
2. **Given** iCal parsing, reservation building, physical-slot observation,
   Keymaster setup/adoption, check-in protection, and diagnostics/event
   recording are tested independently, **When** each concern receives the same
   inputs as the current coordinator, **Then** each produces the same outputs and
   side effects currently produced inside `coordinator.py`.
3. **Given** the refactor is complete, **When** `coordinator.py` is inspected,
   **Then** it remains the Home Assistant `DataUpdateCoordinator` orchestration
   shell and no longer owns the detailed parsing, building, observation, or
   override setup logic.
4. **Given** the complexity suppression is removed, **When** linting runs,
   **Then** the module keeps the legitimate Home Assistant runtime import
   suppression and no longer needs the `complexity/file-too-large` or
   `complexity/function-too-long` suppression.

---

### Edge Cases

- What happens when Home Assistant runtime imports appear unavailable to static
  analysis? The `# aislop-ignore-file ai-slop/hallucinated-import` directive
  remains in `coordinator.py`; only the separate temporary directive for
  `complexity/file-too-large` and `complexity/function-too-long` is removed.
- What happens when calendar data is temporarily unavailable, invalid, empty, or
  stale? Existing cache fallback, miss counter, warning, and UpdateFailed
  behavior remain unchanged.
- What happens when Keymaster entities are missing, unreadable, unavailable,
  partially reset, named but code-less, coded but unnamed, disabled, or missing
  date ranges? Current conservative slot classification, placeholder adoption,
  forced reset, and blocked/retry behavior are preserved.
- What happens when the Store is missing, deleted, stale, corrupt, or migrated?
  Store data remains cache-only; physical Keymaster state and current calendar
  data continue to determine correctness exactly as before.
- What happens when reservations have duplicate names, same starts, changed
  dates, shifted buffers, manual PINs, booking aliases, missing UIDs, or active
  check-in state? Current matching, disambiguation, manual code preservation,
  and check-in protection behavior are preserved.
- What happens when diagnostics are disabled or enabled? The same
  keymaster-event ring buffer, redaction behavior, check-in sensor state writes,
  and reconciliation diagnostics are retained.
- What happens when config options change at runtime? The same update listener,
  coordinator config update, override rebootstrap, buffer-time update, child lock
  discovery, and refresh request behavior are retained.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The decomposition MUST preserve all Home Assistant observable
  behavior of `RentalControlCoordinator`, including calendar data, selected
  current event, coordinator availability semantics, entity-facing attributes,
  listener-facing diagnostics, Store writes, Keymaster service calls, logging
  decisions, and refresh scheduling side effects.
- **FR-002**: Existing coordinator-related unit and integration tests MUST pass
  unchanged after the implementation stage; new tests MUST verify behavior
  parity and independently test extracted concerns rather than introduce new
  runtime behavior.
- **FR-003**: The refresh cycle MUST produce equivalent parsed calendar events,
  observed managed slots, regular reservations, ghost reservations, check-in
  protection flags, desired plans, operation results, latest-plan state, latest
  reservation lookup state, and Store metadata for identical inputs.
- **FR-004**: Calendar feed fetching and parsing behavior MUST remain equivalent,
  including HTTP request options, timeout handling, UTF-8 BOM stripping,
  executor usage for iCal and timezone conversion, event filtering, time
  selection, timezone conversion, prefixing, UID normalization, and sorted event
  ordering.
- **FR-005**: Reservation building behavior MUST remain equivalent, including
  slot-name extraction, display-name formatting, trimming, buffer application,
  fingerprint generation, UID aliases, booking aliases, generated code source,
  manual observed PIN preservation, duplicate-name date matching, active-window
  handling, and invalid-reservation skipping.
- **FR-006**: Ghost reservation behavior MUST remain equivalent, including
  missing-count increments, eligibility by status, pending-set aging,
  pending-clear marking, synthetic identity fields, alias and fingerprint
  retention, and the rule that raw PINs are never stored or reconstructed.
- **FR-007**: Physical Keymaster observation MUST remain equivalent, including
  entity IDs read, unreadable-state handling, free/occupied/phantom/unknown
  classification, date-range parsing, enabled-state parsing, last-error
  propagation, and actual-state diagnostics recorded through `EventOverrides`.
- **FR-008**: Keymaster override setup and adoption MUST remain equivalent,
  including first-load bootstrap ordering, partially reset slot handling,
  code-bearing unnamed slot placeholders, skipped unreadable slots, date-range
  defaults, cache-only Store adoption, migration behavior, and no raw PINs in
  persisted data.
- **FR-009**: Check-in protection behavior MUST remain equivalent, including the
  use of the check-in sensor state and attributes, checked-in protection,
  checked-out marking, duplicate-name start/end matching, missing active
  physical stay synthesis, and restore-deferral decisions.
- **FR-010**: Diagnostics and event-recording behavior MUST remain equivalent,
  including `keymaster_event_diagnostics`, reconciliation diagnostics,
  `latest_overflow`, `latest_reconciliation_diagnostics`, actual-state
  snapshots, redaction of raw slot codes, and any check-in sensor state refresh
  triggered by diagnostics changes.
- **FR-011**: The public coordinator surface consumed by production callers MUST
  remain available and behavior-compatible, including the
  `RentalControlCoordinator` class; inherited coordinator behavior such as
  `data`, `name`, `hass`, `last_update_success`, `async_config_entry_first_refresh`,
  `async_refresh`, and `async_request_refresh`; and coordinator members consumed
  by platform setup, calendar entities, event sensors, check-in sensors,
  listeners, switches, utilities, and event override helpers.
- **FR-012**: Specifically, the consumed coordinator members MUST remain
  behavior-compatible: `monitored_locknames`, `device_info`, `entry_id`,
  `unique_id`, `version`, `latest_plan`, `latest_overflow`,
  `latest_reconciliation_diagnostics`, `get_slot_assignment`, `get_slot_code`,
  `get_overflow_reason`, `async_get_events`,
  `async_setup_keymaster_overrides`, `async_load_slot_store`,
  `get_persisted_slot_mappings`, `async_save_slot_store`,
  `async_adopt_keymaster_slots`, `update_config`, `update_event_overrides`,
  `created`, `lockname`, `start_slot`, `max_events`, `event_overrides`,
  `event_prefix`, `code_generator`, `code_length`, `code_buffer_before`,
  `code_buffer_after`, `trim_names`, `max_name_length`, `event`, and
  `keymaster_event_diagnostics`.
- **FR-013**: The project-owned 12-parameter `_find_observed_slot_by_name`
  internal helper MUST be decomposed or grouped so no project-owned function
  exceeds the 6-parameter threshold while preserving current duplicate-name,
  date-window, consumed-slot, prefix, and fallback matching behavior.
- **FR-014**: The consumed 7-parameter `update_event_overrides` behavior MUST be
  represented through a project-owned API that satisfies the 6-parameter
  threshold while preserving all current override update semantics and current
  in-repository caller behavior in `coordinator.py` and `util.py`.
- **FR-015**: The completed implementation MUST remove only the coordinator
  module's temporary `aislop-ignore-file complexity/file-too-large
  complexity/function-too-long` directive after satisfying the active thresholds;
  it MUST keep the separate `aislop-ignore-file ai-slop/hallucinated-import`
  directive for Home Assistant runtime imports.
- **FR-016**: The completed decomposition MUST keep coordinator-related files
  below 400 lines, project-owned functions below 80 lines, and project-owned
  parameter lists at no more than 6 parameters unless an external framework
  signature requires otherwise.
- **FR-017**: The coordinator MUST remain the Home Assistant
  `DataUpdateCoordinator` orchestration shell responsible for lifecycle,
  refresh scheduling, HA state access boundaries, and coordination with the
  reconciliation package, `EventOverrides`, and Keymaster service helpers.
- **FR-018**: Decomposition MUST preserve integration with the existing
  reconciliation package surface used by the coordinator: `DesiredPlan`,
  `ManagedSlot`, `Reservation`, `SlotStatus`, `compute_desired_plan`,
  `extract_booking_aliases`, `make_reservation_fingerprint`, and
  `normalize_slot_name_for_fingerprint`.
- **FR-019**: Refresh hot paths MUST NOT introduce blocking I/O, additional
  coordinator refreshes, additional Home Assistant state writes, extra Store
  authority, or user-visible delays compared with the current implementation.
- **FR-020**: Planning and implementation documentation MUST state that this is a
  behavior-preserving refactor and MUST NOT define new lock-code business rules,
  new calendar parsing semantics, new reconciliation behavior, new sensors, new
  automations, new configuration options, or changed public caller behavior.

### Key Entities

- **Rental Control Coordinator**: The Home Assistant `DataUpdateCoordinator`
  class currently implemented by `RentalControlCoordinator`. It owns setup,
  refresh orchestration, current event exposure, integration device metadata,
  Store coordination, and calls into parsing, reservation, observation,
  reconciliation, override, and service-helper behavior.
- **Calendar Event**: The parsed Home Assistant calendar event produced from the
  configured iCal feed after filtering, timezone handling, event prefixing,
  manual override time selection, and UID normalization.
- **Reservation**: The reconciliation input derived from calendar events or
  ghost Store metadata, including identity key, slot/display name, access
  windows, code source, aliases, missing count, active protection, checkout
  state, and sensor lookup keys.
- **Managed Slot Observation**: The current physical Keymaster slot facts read
  from Home Assistant entities, including name, PIN presence, date range,
  enabled state, classification, last error, and diagnostics snapshot.
- **Event Overrides**: The coordinator-owned override manager that records
  physical slot state, applies desired plans through Keymaster service helpers,
  tracks actual state and errors, and supplies manual override data to parsing
  and reservation building.
- **Cache-Only Slot Store**: Persisted slot metadata used for aliases,
  diagnostics, adoption, migrations, and ghost reservation construction. It must
  remain non-authoritative for correctness when physical Keymaster state and
  calendar data disagree.
- **Desired Plan**: The stateless reconciliation result computed from
  reservations, managed slots, capacity, plan metadata, and configuration, then
  applied through `EventOverrides` during the coordinator refresh cycle.
- **Public Coordinator Surface**: The class, methods, properties, and attributes
  consumed by production modules and tests, including setup, calendar entities,
  event sensors, check-in tracking, listeners, switches, utilities, and service
  helper paths.

## Assumptions

- This specification covers issue #574's spec stage only; planning and
  implementation stages will decide exact module names, file layout, helper
  boundaries, request objects, and compatibility mechanics.
- The live source at the time of this specification is a 2,948-line
  `custom_components/rental_control/coordinator.py` with 12 functions over 80
  lines and two project-owned parameter lists over 6 parameters.
- The heavy functions identified for decomposition are `_build_reservations`,
  `_build_ghost_reservations`, `_ical_parser`, `_observe_managed_slots`,
  `_apply_checkin_protection`, `async_adopt_keymaster_slots`,
  `_find_observed_slot_by_name`, `_async_update_data`,
  `async_setup_keymaster_overrides`, `__init__`,
  `_sync_slot_store_from_plan`, and `update_config`; `update_event_overrides`
  is the additional 7-parameter consumed coordinator method.
- The existing source and tests are the behavior source of truth unless a later
  accepted issue explicitly changes coordinator behavior.
- Existing public production callers are `__init__.py`, `calendar.py`,
  `sensor.py`, `sensors/calsensor.py`, `sensors/checkinsensor.py`,
  `sensors/checkin/*` runtime helpers, `listeners.py`, `switch.py`, `util.py`,
  and event override helper paths. `config_flow.py` does not directly consume
  the coordinator surface in the current source.
- Runtime performance expectations are parity with the current implementation in
  normal Home Assistant operation, not a new user-visible performance feature.

## Non-Goals

- Changing calendar parsing rules, event inclusion/exclusion, timezone handling,
  check-in or checkout time selection, event prefixes, UID normalization, or
  reservation identity semantics.
- Changing lock-code generation, Keymaster slot selection, physical slot
  observation, manual override behavior, adoption behavior, Store authority,
  buffer handling, check-in protection, diagnostics, or reconciliation results.
- Adding new features, configuration options, sensors, automations, service
  calls, Store recovery workflows, or diagnostics fields.
- Changing the reconciliation algorithm or the public reconciliation API used by
  the coordinator.
- Changing the Home Assistant-facing coordinator public surface, entity public
  behavior, listener behavior, service helper behavior, or public API of other
  Rental Control modules.
- Prescribing exact module names, file layout, class names, request-object
  shapes, or helper function signatures for the plan and implementation stages.
- Closing issue #574 in this specification PR; later implementation work owns
  the runtime refactor.

## Constraints

- No behavior observable by Home Assistant users, automations, dashboards,
  services, logs relied on by tests, diagnostics consumers, or existing tests may
  change as part of this refactor.
- The coordinator refresh path is safety-critical and performance-sensitive; it
  MUST remain asynchronous where it is asynchronous today and MUST NOT add
  blocking I/O, extra refreshes, extra state writes, or extra Store authority.
- Integration with the just-decomposed reconciliation package and existing
  Keymaster service helpers MUST be preserved.
- Store semantics MUST remain cache-only. Missing, stale, deleted, migrated, or
  corrupt Store data MUST NOT become required for physical slot correctness or
  duplicate-prevention safety.
- The final implementation MUST remove the coordinator module's temporary
  `aislop-ignore-file complexity/file-too-large complexity/function-too-long`
  directive by satisfying the underlying thresholds.
- The final implementation MUST retain the separate
  `aislop-ignore-file ai-slop/hallucinated-import` directive because Home
  Assistant runtime imports are legitimate in this integration.
- This specification stage is documentation-only and MUST NOT include production
  code changes.

## Security Considerations

- The coordinator indirectly controls physical property access through calendar
  data, generated or manually observed codes, Keymaster slot state, and
  reconciliation actions. Incorrect behavior can lock out valid guests or leave
  stale access active for prior guests.
- Decomposition must not make persisted Store data authoritative over current
  physical Keymaster state and current calendar data.
- Diagnostics, Store metadata, and plan data must continue to avoid exposing raw
  slot PINs beyond what existing Rental Control behavior already exposes.
- Conservative handling of unreadable Keymaster states, partially cleared slots,
  unknown date ranges, duplicate guest names, and active checked-in guests is a
  safety requirement and must be preserved before any complexity improvement is
  considered successful.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of existing coordinator-related unit and integration tests
  pass unchanged after the implementation stage completes, including
  `test_coordinator.py`, `test_coordinator_buffer_update.py`,
  `test_refresh_cycle.py`, `test_event_overrides.py`,
  `test_slot_reconciliation.py`, `test_calendar.py`, `test_sensors.py`,
  `test_checkin_tracking.py`, and `test_keymaster_event_diagnostics.py`.
- **SC-002**: For identical calendar, configuration, Store, check-in sensor, and
  Keymaster state inputs, the refresh cycle produces equivalent serialized
  calendar events, managed-slot observations, reservations, desired plans,
  operation results, latest-plan diagnostics, Store metadata, and entity-facing
  coordinator data in 100% of covered regression scenarios.
- **SC-003**: In 100% of iCal parsing fixtures, event inclusion/exclusion,
  summary prefixing, start and end datetimes, descriptions, locations, UIDs,
  order, and Honor Event Times/manual override behavior match the current
  implementation.
- **SC-004**: In 100% of reservation and ghost-reservation regression
  scenarios, identity keys, aliases, display slot names, generated or preserved
  codes, buffer windows, missing counts, fingerprint history, checked-out flags,
  and active protection flags match the current implementation.
- **SC-005**: In 100% of Keymaster observation, override setup, adoption,
  buffer-update, and deleted/missing Store scenarios, physical slot
  classifications, service calls, Store updates, actual-state diagnostics, and
  raw-PIN redaction match the current implementation.
- **SC-006**: All production modules that currently import or consume
  `RentalControlCoordinator` continue to run without behavior changes, and
  existing tests that import, construct, mock, or inspect the coordinator require
  no rewrite for behavior assertions.
- **SC-007**: The decomposed coordinator feature area contains no files of 400
  lines or more, no project-owned functions of 80 lines or more, and no
  project-owned parameter lists over 6 parameters.
- **SC-008**: The temporary coordinator complexity suppression is removed in the
  implementation stage, the Home Assistant runtime import suppression remains,
  and active complexity checks pass without suppressing the decomposed
  coordinator behavior.
- **SC-009**: Normal refresh processing performs no additional Home Assistant
  state writes, coordinator refreshes, blocking I/O, authoritative Store reads,
  or user-visible delays compared with the current implementation.
- **SC-010**: No production-code changes are included in this specification PR;
  the PR contains only the feature specification for the #574 decomposition
  pipeline.
