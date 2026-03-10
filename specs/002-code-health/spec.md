<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Feature Specification: Code Health Improvement

**Feature Branch**: `002-code-health`
**Created**: 2026-03-10
**Status**: Draft
**Input**: Improve the overall code health of the Rental Control integration based on the code review findings in `code-reviews/20260310-rc_review.md`. Fix all identified bugs and logic issues, add missing error handling around calendar fetching and parsing so failures don't crash the integration, convert eager log formatting to lazy evaluation for performance, modernize the codebase by replacing deprecated patterns with current idioms, remove dead code and stale comments, and close test coverage gaps — particularly around lock slot management and network error scenarios. Exclude any large architectural refactors such as migrating the coordinator base class, changing UUID generation, or redesigning the polling model — those are separate efforts.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Integration Survives Calendar Failures (Priority: P1)

As a property manager running Rental Control, when my calendar provider
experiences an outage, returns malformed data, or times out, the
integration continues operating with the most recently known-good
calendar data rather than crashing or becoming unavailable. I am not
locked out of my Home Assistant dashboard or left with stale lock codes
because a transient upstream error cascaded through the system.

**Why this priority**: Calendar fetch failures are the most common
real-world fault. If the integration crashes on bad upstream data, all
downstream automation (lock codes, sensors, notifications) stops working.
This directly impacts tenant access to rental properties.

**Independent Test**: Can be fully tested by simulating calendar fetch
errors (timeout, malformed response, server error) and verifying the
integration remains available with its previous calendar state.

**Acceptance Scenarios**:

1. **Given** the integration is running with a valid calendar,
   **When** the calendar provider returns an HTTP error,
   **Then** the integration logs a warning and continues with the
   previously fetched calendar data.
2. **Given** the integration is running with a valid calendar,
   **When** the calendar provider returns malformed data that cannot
   be parsed,
   **Then** the integration logs an error and preserves the existing
   calendar unchanged.
3. **Given** the integration is running with a valid calendar,
   **When** the calendar request times out,
   **Then** the integration logs a warning and retries at the next
   scheduled refresh interval.
4. **Given** the integration is running with a valid calendar
   containing more than one event,
   **When** a calendar refresh returns zero events,
   **Then** the system tracks the miss (just as it does for
   single-event calendars) rather than silently keeping stale data
   forever.

---

### User Story 2 - Lock Slot Changes Complete Fully or Not at All (Priority: P1)

As a property manager, when the system sets or clears a door code for a
guest reservation, all of the related slot changes (pin, name, dates,
enabled flag) either complete together or none of them apply. A partial
update — such as a pin being set but the slot never being enabled —
must not occur even if one of the intermediate steps encounters an error.

**Why this priority**: Partial lock slot updates are a security and
usability hazard. A guest could arrive and be unable to enter the
property, or an old code could remain active past checkout.

**Independent Test**: Can be fully tested by simulating a service call
failure partway through a slot update sequence and verifying that the
slot is left in a safe, consistent state.

**Acceptance Scenarios**:

1. **Given** a new reservation needs a door code,
   **When** one of the slot configuration steps fails,
   **Then** the error is logged, remaining steps are not silently
   abandoned, and the system reports the failure.
2. **Given** multiple sensor updates are triggered simultaneously,
   **When** one sensor update fails,
   **Then** the remaining sensor updates still complete successfully.

---

### User Story 3 - Improved Test Coverage for Critical Paths (Priority: P2)

As a developer contributing to Rental Control, the lock slot management
functions and network error paths have automated test coverage so that
future changes to these critical areas are caught by the test suite
before reaching production.

**Why this priority**: The lock slot management and calendar fetch logic
are the most complex and side-effect-heavy parts of the codebase. Without
tests, regressions in these areas go undetected until they affect real
tenants.

**Independent Test**: Can be verified by running the test suite and
confirming that the previously uncovered lock slot functions and network
error paths now have passing tests.

**Acceptance Scenarios**:

1. **Given** the existing test suite,
   **When** tests are run,
   **Then** the lock slot management functions (clear code, set code,
   update times, state change handler) all have dedicated test coverage.
2. **Given** the existing test suite,
   **When** tests are run,
   **Then** calendar fetch error scenarios (timeout, malformed data,
   timezone conversion failure) all have dedicated test coverage.
3. **Given** the existing test suite,
   **When** tests are run,
   **Then** the Keymaster slot bootstrapping path during coordinator
   startup has dedicated test coverage.

---

### User Story 4 - Logging Performance Improvement (Priority: P3)

As a user running Rental Control on resource-constrained hardware
(e.g., a Raspberry Pi), log message formatting is deferred so that
string interpolation work is not performed unless the relevant log level
is actually enabled. This reduces unnecessary CPU usage during normal
operation.

**Why this priority**: Performance matters on low-powered devices, but
this is an optimization rather than a correctness issue. No user-visible
behavior changes.

**Independent Test**: Can be verified by confirming that all log
statements use deferred formatting rather than eager string
interpolation.

**Acceptance Scenarios**:

1. **Given** the codebase,
   **When** a code review or linting check is performed,
   **Then** no log statements use eager string formatting (all use
   deferred parameter substitution).

---

### User Story 5 - Modernized and Cleaned-Up Codebase (Priority: P3)

As a developer, the codebase follows current language idioms, contains no
dead code, stale comments, or deprecated patterns, and uses the modern
integration patterns expected by the Home Assistant platform. This
reduces confusion for new contributors and avoids warnings from platform
tooling.

**Why this priority**: Housekeeping work that improves long-term
maintainability but has no direct user-visible impact.

**Independent Test**: Can be verified by confirming that all deprecated
patterns, dead code, stale comments, and legacy idioms identified in the
code review have been addressed.

**Acceptance Scenarios**:

1. **Given** the codebase,
   **When** reviewed against the code review findings,
   **Then** all dead code (unreachable exception handlers, unused
   imports) has been removed.
2. **Given** the codebase,
   **When** reviewed against the code review findings,
   **Then** all stale comments and leftover refactoring artifacts have
   been removed.
3. **Given** the codebase,
   **When** reviewed against the code review findings,
   **Then** deprecated type annotations have been replaced with current
   built-in equivalents.
4. **Given** the codebase,
   **When** reviewed against the code review findings,
   **Then** legacy integration setup patterns (unused config schema,
   synchronous setup function, legacy handler registration, legacy
   platform unloading) have been replaced with modern equivalents.
5. **Given** the codebase,
   **When** reviewed against the code review findings,
   **Then** file path operations use a consistent approach throughout.
6. **Given** the codebase,
   **When** reviewed against the code review findings,
   **Then** idiomatic null-checking patterns are used consistently.
7. **Given** the codebase,
   **When** reviewed against the code review findings,
   **Then** the docstring typo ("EventOVerrides") is corrected.
8. **Given** the codebase,
   **When** reviewed against the code review findings,
   **Then** the commented-out function parameter is removed.

---

### User Story 6 - Configuration Consistency for Miss Tracking (Priority: P3)

As a user or developer, the calendar refresh miss-tracking threshold
is handled consistently — it is either a user-configurable option
exposed in the setup flow, or it is treated as an internal constant
that is not read from configuration data at all.

**Why this priority**: A minor inconsistency that could confuse
developers but has no immediate user impact.

**Independent Test**: Can be verified by confirming the miss threshold
is either present in the configuration UI with appropriate validation,
or is referenced only as an internal constant.

**Acceptance Scenarios**:

1. **Given** the integration configuration,
   **When** the miss-tracking threshold is examined,
   **Then** it is handled consistently as either a user-facing setting
   or a pure internal constant (not a hybrid of both).

---

### Edge Cases

- What happens when the calendar provider returns a valid HTTP response
  but with a non-calendar content type (e.g., an HTML error page)?
- What happens when multiple concurrent calendar refreshes are triggered
  (e.g., config change during a scheduled refresh)?
- What happens when a lock slot service call times out but the
  underlying action actually succeeded on the lock?
- What happens when the integration starts up and the Keymaster
  entities are not yet available (still loading)?
- What happens when a calendar refresh returns events that overlap
  with currently assigned lock slots whose overrides have been manually
  edited by the user?

## Requirements *(mandatory)*

### Functional Requirements

#### Error Handling & Resilience

- **FR-001**: The system MUST catch and gracefully handle all errors
  during calendar data fetching (network errors, timeouts, HTTP errors)
  without crashing the integration.
- **FR-002**: The system MUST catch and gracefully handle all errors
  during calendar data parsing (malformed data, timezone conversion
  failures) without crashing the integration.
- **FR-003**: When a calendar fetch or parse fails, the system MUST
  preserve the most recently successful calendar data and continue
  operating with it.
- **FR-004**: When multiple concurrent operations are dispatched (e.g.,
  multiple service calls or sensor updates), a failure in one operation
  MUST NOT silently cancel the remaining operations.
- **FR-005**: When a calendar with more than one event receives an empty
  refresh, the system MUST track the miss using the same mechanism as
  single-event calendars, rather than silently preserving stale data
  indefinitely.
- **FR-006**: The calendar readiness and override-loaded state tracking
  MUST be reviewed and simplified so that readiness is determined through
  a single, clear path regardless of whether a lock manager is
  configured.

#### Bug Fixes

- **FR-007**: The system MUST remove the unreachable error handler
  around calendar event description retrieval (the handler catches an
  error that the underlying method call can never raise).
- **FR-008**: Debug logging of calendar events MUST log the actual event
  data, not the event type/class name.
- **FR-009**: The non-reserved event filtering check MUST use an
  idiomatic null-check pattern rather than the current non-standard
  type comparison approach.

#### Performance

- **FR-010**: All log statements MUST use deferred formatting (parameter
  substitution) rather than eager string interpolation, so that
  formatting work is skipped when the log level is not active.

#### Code Modernization

- **FR-011**: All type annotations MUST use current built-in generic
  types (e.g., `dict`, `list`, `X | Y`) rather than legacy
  `typing`-module equivalents.
- **FR-012**: All unused imports and linter suppression comments MUST be
  removed.
- **FR-013**: All stale comments and leftover refactoring artifacts MUST
  be removed.
- **FR-014**: All inert linter-directive comments for linters not used
  by the project MUST be removed.
- **FR-015**: The unnecessary empty configuration schema and synchronous
  setup function MUST be removed since the integration is config-flow
  only.
- **FR-016**: The legacy config flow handler registration decorator MUST
  be removed since the manifest already declares config flow support.
- **FR-017**: The legacy platform unloading pattern MUST be replaced
  with the current single-call platform unloading approach.
- **FR-018**: File path operations MUST use a consistent approach
  throughout the codebase (matching the style used in the test suite).
- **FR-019**: The commented-out function parameter in the lock manager
  lookup function MUST be removed.
- **FR-020**: The docstring typo ("EventOVerrides") MUST be corrected.

#### Configuration

- **FR-021**: The calendar refresh miss-tracking threshold MUST be
  handled consistently — either exposed as a user-configurable option in
  the setup flow, or treated purely as an internal constant that is not
  read from configuration data.

#### Testing

- **FR-022**: The lock slot management functions (clear code, set code,
  update times, state change handler) MUST have automated test coverage.
- **FR-023**: Calendar fetch error scenarios (timeout, malformed data,
  timezone conversion failure, non-200 HTTP responses) MUST have
  automated test coverage.
- **FR-024**: The Keymaster slot bootstrapping path during coordinator
  startup MUST have automated test coverage.

### Assumptions

- The existing 278 tests and 90% coverage represent a healthy baseline;
  this effort builds on that foundation.
- All changes MUST maintain backward compatibility with existing user
  configurations (no breaking changes to config schema or entity IDs).
- The code review document `code-reviews/20260310-rc_review.md` is the
  authoritative source of findings for this effort.
- The project uses a pre-commit pipeline (ruff, mypy, interrogate,
  reuse) that all changes must continue to pass.

### Exclusions

The following items from the code review are **explicitly out of scope**:

- **Coordinator base class migration** (review §1.1): Migrating to the
  platform's built-in data update coordinator is a major architectural
  change requiring its own specification.
- **Coordinator class extraction / refactoring** (review §1.2): Breaking
  the coordinator into smaller classes is a separate refactoring effort.
- **UUID generation algorithm change** (review §4.1): Changing the hash
  function would break all existing entity IDs for current users.
- **Door code random number generator change** (review §4.2): The
  current approach is acceptable for the use case.
- **Polling model redesign** (review §7.3): Resolving the polling
  conflict between the calendar entity and coordinator requires design
  work beyond a code health pass.
- **Module-level timezone computation** (review §8.3): Minor testability
  concern with no practical impact.
- **Version 0.0.0 placeholder** (review §6.1): Handled by the release
  pipeline, not a code health issue.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The integration continues to function normally (all
  existing sensors, calendar, and lock code management working) after
  all changes are applied — zero regressions in existing behavior.
- **SC-002**: All existing tests continue to pass after changes.
- **SC-003**: No log statements in the codebase use eager string
  formatting (0 instances of f-string or `.format()` inside log calls).
- **SC-004**: Test coverage for the two lowest-covered source files
  increases to at least 85% each (from 77% and 81% respectively).
- **SC-005**: Calendar fetch/parse errors are handled gracefully — the
  integration remains available and preserves its previous state when
  the upstream calendar is unreachable or returns bad data.
- **SC-006**: All code modernization items from the review (dead code
  removal, legacy pattern replacement, type annotation updates) are
  addressed — zero remaining findings from the code review summary
  table that are in scope.
- **SC-007**: The full pre-commit pipeline (linting, type checking,
  docstring coverage, license compliance) passes cleanly after all
  changes.
