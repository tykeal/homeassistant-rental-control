<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Feature Specification: Explicit Entry Data Guards

**Feature Branch**: `010-explicit-entry-guards`
**Created**: 2026-06-18
**Status**: Draft
**Input**: User description: "Replace implicit empty entry-data defaults with explicit missing-data guards for issue #571. Missing integration domain or entry data must be handled predictably while preserving existing behavior for loaded entries."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Preserve Loaded Entry Behavior (Priority: P1)

As a Rental Control maintainer, I want the existing configuration update, listener refresh, event forwarding, monitoring, early-expiry, and switch-registration behavior to remain unchanged when an integration entry is loaded normally, so this quality refactor introduces no user-visible regression.

**Why this priority**: The feature exists to clarify error paths, not to change normal behavior. Loaded entries represent the primary production path for property managers and must remain stable.

**Independent Test**: Can be fully tested by exercising each affected operation with a normally loaded entry and verifying the same state changes, listener behavior, event forwarding, monitoring decisions, and switch availability as before the refactor.

**Acceptance Scenarios**:

1. **Given** a Rental Control entry is loaded with its shared entry data present, **When** configuration options are applied, **Then** the entry update and listener refresh complete as they did before.
2. **Given** a loaded entry has the required monitoring and check-in components available, **When** a valid lock event is received, **Then** the event is evaluated and forwarded exactly as before.
3. **Given** a loaded entry has early checkout expiry enabled, **When** checkout handling reaches the early-expiry decision, **Then** the expiry behavior remains unchanged.
4. **Given** a loaded entry adds its monitoring switch, **When** the switch becomes available, **Then** other integration components can discover it as before.

---

### User Story 2 - Short-Circuit Missing Entry Data Safely (Priority: P1)

As a Rental Control maintainer, I want every affected path that depends on shared entry data to recognize when the integration domain or specific entry data is absent, so unload and setup race windows are explicit, safe, and easy to reason about.

**Why this priority**: The issue targets hidden missing-data cases. Making absence explicit prevents accidental reliance on anonymous empty state and makes future maintenance safer.

**Independent Test**: Can be fully tested by invoking each affected operation while integration domain data or entry-specific data is absent and verifying the operation exits safely without raising, without creating phantom empty state, and without reporting that work was completed.

**Acceptance Scenarios**:

1. **Given** integration domain data is absent, **When** an affected operation tries to use entry-specific state, **Then** it short-circuits safely without an unhandled exception.
2. **Given** integration domain data exists but the target entry data is absent, **When** an affected operation tries to use that entry, **Then** it short-circuits safely without creating or mutating throwaway entry state.
3. **Given** a missing-data condition occurs during unload or startup ordering, **When** the operation exits early, **Then** later normal loading can still establish the expected shared entry state.

---

### User Story 3 - Keep Supporting Component Absence Predictable (Priority: P2)

As a Rental Control maintainer, I want missing supporting components within otherwise present entry data to follow explicit, existing outcomes, so event handling and sensor decisions remain predictable during startup ordering.

**Why this priority**: Some affected paths intentionally tolerate supporting components that are not available yet. The refactor must keep those expected fallbacks while clarifying the difference between a missing entry and a missing component within an entry.

**Independent Test**: Can be fully tested by providing entry data without one supporting component at a time and verifying each path follows its documented non-action or fallback behavior.

**Acceptance Scenarios**:

1. **Given** entry data is present but the check-in sensor is not yet available, **When** a valid lock event is processed, **Then** the event is not forwarded and the missing sensor outcome is recorded predictably.
2. **Given** entry data is present but the monitoring switch is not yet available, **When** monitoring status is evaluated during setup ordering, **Then** the existing configured fallback is used where applicable.
3. **Given** entry data is present but the early-expiry switch is not available, **When** checkout handling reaches early-expiry evaluation, **Then** checkout continues without early-expiry adjustment.

---

### User Story 4 - Bound the Refactor to the Reported Quality Issue (Priority: P3)

As a Rental Control maintainer, I want the refactor scope limited to the six issue-reported entry-data access paths, so the change remains low-risk and reviewable.

**Why this priority**: The issue is a medium-priority code-quality cleanup. Clear scope prevents unrelated behavior changes from entering the implementation stage.

**Independent Test**: Can be fully tested by reviewing the issue-reported paths and verifying each one has explicit missing-data handling while unrelated code paths are unchanged unless directly necessary to preserve the same contract.

**Acceptance Scenarios**:

1. **Given** the six issue-reported entry-data access paths are reviewed, **When** the implementation is complete, **Then** each path has an explicit missing-domain or missing-entry outcome.
2. **Given** code outside the reported entry-data access paths is reviewed, **When** the implementation is complete, **Then** unrelated behavior remains unchanged.

---

### Edge Cases

- What happens when integration domain data is absent because the entry has already unloaded? The affected operation exits safely and performs no entry-specific work.
- What happens when integration domain data exists but the requested entry is absent? The affected operation exits safely and does not create, mutate, or rely on phantom empty entry state.
- What happens when entry data is present but a supporting component is not registered yet? The operation follows its existing non-action or fallback behavior for that component and does not treat the missing component as a valid completed action.
- What happens when a transient setup or unload race resolves later? The later loaded path can populate and use the real entry data normally; the earlier missing-data path must not leave behind throwaway state that affects future behavior.
- What happens when all expected data is present? The refactor must preserve the existing external behavior with no observable change for users.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST distinguish absent integration domain data from present domain data before any affected operation uses entry-specific shared state.
- **FR-002**: The system MUST distinguish absent entry data from present entry data before any affected operation uses components or coordinator state associated with that entry.
- **FR-003**: When integration domain data or entry data is absent, the affected operation MUST short-circuit safely without raising an unhandled exception.
- **FR-004**: When integration domain data or entry data is absent, the affected operation MUST NOT create, mutate, or rely on throwaway empty entry state.
- **FR-005**: When entry data is present and contains the expected supporting components, the affected operations MUST preserve existing external behavior for configuration updates, listener lifecycle, lock-event evaluation, check-in monitoring, early checkout expiry, and monitoring switch availability.
- **FR-006**: When entry data is present but a supporting component is absent, each affected operation MUST follow the existing safe non-action or fallback outcome for that component.
- **FR-007**: Event-related outcomes MUST remain truthful: a lock event MUST NOT be treated as accepted or forwarded when required entry data or required supporting components are unavailable.
- **FR-008**: Missing-data handling MUST be explicit for all six entry-data access paths reported in issue #571.
- **FR-009**: The implementation stage MUST include verification for both normal loaded-entry behavior and missing domain or entry data behavior for the affected operations.
- **FR-010**: The refactor MUST NOT introduce new user-facing configuration, entity state, service behavior, or migration behavior.

### Key Entities

- **Integration Domain Data**: The shared runtime storage for Rental Control entries. Its absence represents the integration not being available for the current operation.
- **Entry Data**: The shared runtime state for one configured Rental Control entry. It contains the coordinator and supporting components needed by the affected operations.
- **Supporting Component**: An entry-scoped object such as a sensor or switch that may be registered after entry data exists and may be temporarily absent during setup or unload ordering.
- **Affected Operation**: One of the issue-reported paths that reads shared entry data while updating configuration, refreshing listeners, handling lock events, evaluating check-in monitoring, evaluating early checkout expiry, or registering the monitoring switch.

## Assumptions

- This is a refactor with no intended behavior change for the present-data path; external behavior for normally loaded entries is preserved.
- The in-scope call sites are the six locations reported in issue #571: three in the integration setup module, two in the check-in sensor module, and one in the switch module.
- Missing integration domain data or entry data represents a safe no-work condition for the affected operations rather than a recoverable user action.
- Existing behavior for a missing supporting component remains valid unless it conflicts with the explicit missing-entry contract.
- Broader cleanup of unrelated shared-data access patterns belongs to separate issues or later stages, not this spec.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For 100% of the six issue-reported affected operations, missing integration domain data or missing entry data results in zero unhandled exceptions and zero phantom empty entry-state mutations.
- **SC-002**: For 100% of regression scenarios with a normally loaded entry, externally observable behavior remains unchanged for configuration updates, listener lifecycle, lock-event forwarding, check-in monitoring, early checkout expiry, and monitoring switch availability.
- **SC-003**: A maintainer can identify a distinct missing-domain or missing-entry outcome for 100% of the affected operations without relying on anonymous empty state.
- **SC-004**: In 100% of event-handling scenarios where required entry data or required supporting components are unavailable, the event is not reported as successfully accepted or forwarded.
- **SC-005**: The implementation changes introduce zero new user-facing configuration options, entity states, service inputs, migrations, or behavior changes outside the missing-data handling contract.
