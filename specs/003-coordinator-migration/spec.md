<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Feature Specification: Coordinator Base Class Migration

**Feature Branch**: `003-coordinator-migration`
**Created**: 2026-03-11
**Status**: Draft
**Input**: Migrate the hand-rolled coordinator to the platform's
built-in data update coordinator base class, as identified in the
code review (§1.1) and explicitly deferred from spec 002.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Platform-Standard Data Refresh (Priority: P1)

As a user of the Rental Control integration, I expect the
integration to follow the platform's standard data refresh
conventions so that calendar data is fetched on a reliable,
platform-managed schedule and any transient failures are retried
automatically without manual intervention.

**Why this priority**: The core value of this migration is adopting
the platform's built-in refresh lifecycle. Every other improvement
depends on the coordinator inheriting from the standard base class.

**Independent Test**: After migrating to the platform base class,
the integration refreshes calendar data on the configured interval
without any manual trigger, and entities receive updates through the
platform's standard subscription mechanism.

**Acceptance Scenarios**:

1. **Given** the integration is set up with a valid calendar URL
   and a configured refresh interval, **When** the refresh interval
   elapses, **Then** the coordinator fetches new calendar data
   automatically through the platform's refresh lifecycle.
2. **Given** the integration has just been loaded, **When** the
   platform calls the coordinator's first refresh, **Then** calendar
   data is fetched and all entities are populated before the UI
   renders them.
3. **Given** entities are registered with the coordinator, **When**
   new calendar data arrives, **Then** all entities are notified of
   the update through the platform's listener mechanism rather than
   being polled individually.

---

### User Story 2 — Automatic Error Recovery (Priority: P1)

As a user, I expect the integration to handle temporary upstream
failures (network timeouts, HTTP errors, malformed calendar data)
gracefully, retrying automatically and preserving the last known
good state so that my dashboard and automations continue working
during brief outages.

**Why this priority**: Without built-in error tracking and retry,
a single failed fetch can leave entities in an unknown state. This
is the second most impactful benefit of the migration.

**Independent Test**: Simulate an unreachable calendar URL, verify
the integration preserves its previous state, logs a warning, and
successfully recovers when the URL becomes reachable again — all
without user intervention.

**Acceptance Scenarios**:

1. **Given** the calendar URL is temporarily unreachable, **When**
   a scheduled refresh occurs, **Then** the coordinator logs the
   failure, preserves the previous calendar data, and retries on
   the next interval.
2. **Given** the upstream calendar returns malformed data, **When**
   the coordinator attempts to parse it, **Then** the error is
   caught, the previous valid calendar state is retained, and the
   integration remains available.
3. **Given** a transient failure has occurred, **When** the next
   scheduled refresh succeeds, **Then** the coordinator's error
   state clears and entities reflect the freshly fetched data.
4. **Given** multiple consecutive failures occur, **When** the
   platform queries the coordinator's health, **Then** the
   coordinator accurately reports its last-successful-update status
   to the platform.

---

### User Story 3 — Transparent Migration (Priority: P1)

As a user upgrading from the previous version, I expect the
migration to be completely transparent — no reconfiguration, no
lost data, and no change in the integration's visible behavior or
entity structure.

**Why this priority**: A migration that breaks existing setups or
requires reconfiguration would be worse than the status quo. Zero
regression is a hard requirement.

**Independent Test**: Load a configuration entry created under the
previous version, verify all entities, sensors, calendar events,
and Keymaster slot assignments continue to function identically.

**Acceptance Scenarios**:

1. **Given** an existing installation with configured calendar
   entries and Keymaster slot assignments, **When** the user
   upgrades to the migrated version, **Then** all entities appear
   with the same names, unique IDs, and states as before.
2. **Given** automations reference sensors or calendar entities
   from this integration, **When** the upgrade occurs, **Then**
   all automations continue to trigger correctly.
3. **Given** the integration was previously tracking event
   overrides for Keymaster slots, **When** the coordinator
   migrates, **Then** slot assignments and door codes remain
   intact.

---

### User Story 4 — Entity Availability Reporting (Priority: P2)

As a user viewing my dashboard, I expect entities to accurately
reflect their availability — showing as "unavailable" only when
the coordinator genuinely cannot provide data, and recovering
automatically once data is available again.

**Why this priority**: The platform base class provides built-in
availability tracking (`last_update_success`) that the current
implementation lacks. This is a direct benefit of the migration
but secondary to the core refresh and error recovery stories.

**Independent Test**: Cause a sustained calendar failure, verify
entities show as unavailable in the UI, then restore the calendar
and verify entities recover to available.

**Acceptance Scenarios**:

1. **Given** the coordinator has never successfully fetched data,
   **When** the user views the dashboard, **Then** entities report
   as unavailable.
2. **Given** the coordinator has previously fetched data
   successfully but the latest refresh failed, **When** the user
   views the dashboard, **Then** entities remain available with
   the last known good data.
3. **Given** the coordinator has been failing for an extended
   period, **When** a refresh finally succeeds, **Then** entities
   immediately update and report as available.

---

### Edge Cases

- What happens when the integration loads for the first time and
  the calendar URL is unreachable? Entities should report as
  unavailable and the coordinator should keep retrying.
- What happens when the configured refresh interval is changed
  via the options flow while the coordinator is running? The new
  interval should take effect on the next refresh cycle.
- What happens when the Keymaster integration has not yet loaded
  when the coordinator starts? The coordinator should gracefully
  handle missing Keymaster entities and populate slots once they
  become available.
- What happens when the coordinator is shut down mid-refresh
  (e.g., during a Home Assistant restart)? The in-flight request
  should be cancelled cleanly without leaving dangling tasks.
- What happens when two entities request a refresh at the same
  time? The platform base class should deduplicate concurrent
  refresh requests automatically.

## Requirements *(mandatory)*

### Functional Requirements

#### Base Class Adoption

- **FR-001**: The coordinator MUST inherit from the platform's
  standard data update coordinator base class, gaining its
  built-in refresh scheduling, error tracking, and entity
  subscription model.
- **FR-002**: The coordinator MUST delegate refresh scheduling
  entirely to the platform base class, removing the custom
  `next_refresh` timestamp comparison logic.
- **FR-003**: The coordinator MUST implement the platform's
  standard data-fetch callback so that the base class manages
  the fetch-parse-distribute lifecycle.

#### Entity Integration

- **FR-004**: All entities (calendar, sensors) MUST receive data
  updates through the platform's standard coordinator listener
  mechanism instead of manually calling the coordinator's update
  method.
- **FR-005**: Entities MUST derive their availability from the
  coordinator's built-in success/failure tracking rather than
  custom flags.
- **FR-006**: The calendar entity MUST continue to support the
  platform's `async_get_events` interface for date-range queries
  without changes to the event retrieval behavior.

#### Error Handling and Recovery

- **FR-007**: The coordinator MUST preserve the last successfully
  fetched calendar data when a refresh fails, ensuring entities
  continue to serve stale-but-valid data during outages.
- **FR-008**: The coordinator MUST surface refresh failures
  through the platform's standard error reporting so that the
  platform can display appropriate status in the UI.
- **FR-009**: The coordinator MUST handle all categories of
  refresh failure (network errors, HTTP errors, parse errors,
  timeouts) and report them through the platform's error model.

#### Backward Compatibility

- **FR-010**: The migration MUST NOT change any entity unique IDs,
  entity names, or device registry entries.
- **FR-011**: The migration MUST NOT change the structure or
  content of sensor state attributes.
- **FR-012**: The migration MUST NOT alter the integration's
  configuration flow or stored configuration data.
- **FR-013**: The migration MUST preserve all existing Keymaster
  slot management behavior, including slot bootstrapping on
  startup and event override tracking.

#### Refresh Behavior

- **FR-014**: The coordinator MUST support the user-configured
  refresh interval (in minutes) from the integration's options
  flow.
- **FR-015**: The coordinator MUST perform an initial data fetch
  during integration setup, before entities are created, so that
  entities have data available on first render.
- **FR-016**: The coordinator MUST support on-demand refresh
  requests (e.g., from the platform's reload or refresh service
  calls).

### Key Entities

- **Coordinator**: The central data manager that fetches calendar
  data, parses events, manages Keymaster slot assignments, and
  distributes updates to all dependent entities.
- **Calendar Entity**: Displays the next upcoming event and
  supports date-range event queries. Depends on the coordinator
  for data.
- **Calendar Sensors**: One sensor per event slot, displaying
  event details (check-in/out times, guest name, door code).
  Depends on the coordinator for calendar data and Keymaster
  state.
- **Event Overrides**: Manages per-slot overrides from the
  Keymaster integration, allowing manual door code and schedule
  assignments. Collaborates with the coordinator during the
  update cycle.

## Assumptions

- The platform's standard data update coordinator base class
  supports all refresh patterns needed by this integration
  (periodic interval, on-demand, first-refresh-before-entities).
- The existing Keymaster slot bootstrapping logic (reading entity
  states on startup) can be preserved within the new base class's
  lifecycle hooks without requiring changes to the Keymaster
  integration itself.
- The calendar entity's `async_get_events` method (used for
  date-range queries in the UI) can continue to read directly
  from the coordinator's stored data without triggering an
  additional refresh.
- The miss-tracking logic for consecutive failed refreshes
  (added in spec 002) will be adapted to work with the platform's
  built-in error tracking rather than duplicating it.

## Exclusions

- **Coordinator class extraction / god-class refactoring** (§1.2,
  §1.3 of the code review): Breaking the coordinator into smaller
  classes or extracting the iCal parser is a separate refactoring
  effort. This spec focuses solely on the base class migration.
- **Polling model redesign**: Changing from periodic polling to
  push-based updates (e.g., CalDAV subscriptions) is out of scope.
- **Configuration flow changes**: No changes to the user-facing
  setup or options flow are included in this migration.
- **New feature additions**: This is a pure architectural migration.
  No new user-facing features, sensors, or capabilities are added.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The integration continues to function normally after
  the migration — all existing sensors, calendar, and lock code
  management working — with zero regressions in visible behavior.
- **SC-002**: All existing tests continue to pass after the
  migration, and new tests cover the platform base class lifecycle
  (first refresh, scheduled refresh, error recovery).
- **SC-003**: Entities automatically reflect updated calendar data
  within one refresh interval without any entity manually polling
  the coordinator.
- **SC-004**: When the upstream calendar is unreachable, entities
  remain available with stale data and the coordinator's health
  status accurately reports the failure to the platform.
- **SC-005**: When the upstream calendar recovers after a failure,
  entities update to reflect fresh data within one refresh interval
  without user intervention.
- **SC-006**: The coordinator's custom refresh-timing logic
  (next_refresh timestamp comparison) is fully removed, with all
  scheduling delegated to the platform base class.
- **SC-007**: Test coverage remains at or above 85% for the
  coordinator module after migration.
- **SC-008**: The full pre-commit pipeline (linting, type checking,
  docstring coverage, license compliance) passes cleanly after all
  changes.
