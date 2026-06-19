<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Feature Specification: Decompose Integration Entry Module

**Feature Branch**: `011-decompose-init`
**Created**: 2026-06-19
**Status**: Draft
**Input**: User description: "Decompose `custom_components/rental_control/__init__.py` for issue #572 by extracting migration and keymaster-listener logic into dedicated modules while preserving integration behavior."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Preserve Existing Integration Behavior (Priority: P1)

As a Rental Control maintainer, I want the integration entry module decomposition to preserve setup, unload, migration, update-listener, package-listener, and keymaster-listener behavior exactly as it works today, so the refactor improves reviewability without changing runtime outcomes for property managers or Home Assistant users.

**Why this priority**: The issue is a quality refactor for stable, well-tested code. Any externally visible behavior change would create unnecessary risk for lock-code management and must be excluded from the specification.

**Independent Test**: Can be fully tested by running the existing behavior tests unchanged and verifying that normal setup, unload, configuration migration, listener refresh, package-listener state tracking, and keymaster unlock forwarding produce the same observable outcomes as before.

**Acceptance Scenarios**:

1. **Given** a Rental Control entry is loaded normally, **When** Home Assistant sets up and unloads the integration, **Then** setup, platform forwarding, listener registration, generated-file cleanup, unsubscribe cleanup, and domain-data cleanup behave as before.
2. **Given** an existing Rental Control entry requires migration, **When** Home Assistant invokes the integration migration entry point, **Then** every supported version transition applies the same defaults, removals, and final version updates as before.
3. **Given** keymaster monitoring is configured and a matching unlock event is received, **When** the event satisfies the existing acceptance rules, **Then** the check-in sensor receives the same unlock notification and diagnostic outcome as before.
4. **Given** another module or test imports the integration's public entry points, **When** those imports are evaluated after the refactor, **Then** the package-level names remain available with the same callable behavior.

---

### User Story 2 - Review Migration Logic Independently (Priority: P1)

As a Rental Control maintainer, I want configuration migration responsibilities separated from entry setup orchestration, so each migration step can be reviewed and reasoned about without scanning unrelated setup, unload, or listener code.

**Why this priority**: `async_migrate_entry` is one of the two issue-reported functions over the line-length threshold. Separating migration responsibilities directly addresses the maintainability problem while keeping existing migration semantics stable.

**Independent Test**: Can be fully tested by reviewing the migration responsibility boundary and by exercising each supported migration version transition to confirm the same configuration data and version outcomes as the current implementation.

**Acceptance Scenarios**:

1. **Given** the integration entry module is reviewed, **When** maintainers look for migration behavior, **Then** the detailed migration flow is located in a dedicated migration area rather than mixed into setup and listener orchestration.
2. **Given** a supported entry version from the existing migration range, **When** migration runs, **Then** the entry reaches the same resulting data and version as it did before the decomposition.
3. **Given** an unsupported entry version older than the existing supported range, **When** migration runs, **Then** it fails safely with the same outcome as before.

---

### User Story 3 - Review Keymaster Listener Logic Independently (Priority: P2)

As a Rental Control maintainer, I want keymaster event listener registration and event filtering separated from the integration entry module, so listener behavior can be reviewed independently from setup, unload, update-listener, and migration concerns.

**Why this priority**: `async_register_keymaster_listener` is the largest issue-reported function and contains nested event-handling behavior. Isolating this responsibility improves readability and helps future reviewers verify event acceptance and rejection rules.

**Independent Test**: Can be fully tested by registering the keymaster listener, sending the same accepted and rejected event cases as before, and verifying that listener storage, diagnostics, monitoring checks, and check-in forwarding outcomes are unchanged.

**Acceptance Scenarios**:

1. **Given** an entry has a configured lock name, **When** setup or configuration update starts keymaster monitoring, **Then** the listener is registered once per current lifecycle path and its unsubscribe callback remains tracked for unload or refresh cleanup.
2. **Given** keymaster events arrive for unmonitored locks, non-unlock states, zero or out-of-range slots, missing check-in sensors, or disabled monitoring, **When** the listener evaluates them, **Then** each event is rejected in the same way and with the same diagnostic disposition as before.
3. **Given** a valid monitored unlock event arrives while monitoring is enabled, **When** the listener evaluates it, **Then** the check-in sensor receives the same slot number and lock name as before.

---

### User Story 4 - Bound the Refactor to the Reported Complexity Issue (Priority: P3)

As a Rental Control maintainer, I want the decomposition limited to the issue-reported migration and keymaster-listener responsibilities, so the change remains small, reviewable, and suitable for a medium-priority quality cleanup.

**Why this priority**: The goal is to satisfy the reported file-size and function-length thresholds, not to redesign integration behavior or address unrelated complexity warnings.

**Independent Test**: Can be fully tested by reviewing the changed files and confirming the decomposition addresses the reported `__init__.py` file-size and function-length violations without altering unrelated feature behavior or unrelated complexity findings.

**Acceptance Scenarios**:

1. **Given** the refactor is complete, **When** maintainers inspect `custom_components/rental_control/__init__.py`, **Then** it is focused on integration entry orchestration and no longer contains the detailed migration or keymaster event-handling bodies.
2. **Given** complexity results are reviewed for the affected entry module, **When** the reported thresholds are checked, **Then** the entry module is below the file-size threshold and its functions are below the function-length threshold.
3. **Given** unrelated modules or warnings are reviewed, **When** the refactor scope is evaluated, **Then** no unrelated behavior cleanup or warning remediation has been included unless directly required to preserve the extracted responsibilities.

---

### Edge Cases

- What happens when a config entry is older than the minimum supported migration version? Migration fails with the same safe outcome and messaging as before.
- What happens when a config entry must pass through multiple migration versions? The same ordered version transitions, defaults, removals, and final version result are preserved.
- What happens when no lock is configured for an entry? Keymaster listener registration continues to be skipped in the same setup and update paths as before.
- What happens when keymaster events come from unmonitored locks? They continue to be ignored early and do not flood diagnostics.
- What happens when keymaster diagnostics are enabled for rejected or accepted monitored events? The same diagnostic dispositions and sensor refresh behavior are preserved.
- What happens when entry data, the check-in sensor, or the monitoring switch is temporarily unavailable while a keymaster event is processed? The same safe non-action or rejection outcome is preserved.
- What happens when existing tests or modules import package-level entry points from the integration package? Those imports remain valid, including re-exporting names if needed.
- What happens to unrelated complexity warnings in other files? They remain out of scope for this refactor stage.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The refactor MUST preserve all externally observable behavior for integration setup, unload, migration, update-listener refresh, package-listener tracking, keymaster listener registration, keymaster event filtering, diagnostics, and check-in forwarding.
- **FR-002**: Configuration migration responsibilities MUST live in a dedicated migration module, with `migrations.py` as the default target for the implementation stage.
- **FR-003**: The migration flow MUST preserve the existing supported version range, unsupported-version outcome, ordered version transitions, default values, removed values, update-entry calls, and final success result.
- **FR-004**: Keymaster listener registration and event-handling responsibilities MUST live in a dedicated listener module, with `listeners.py` as the default target for the implementation stage unless planning selects the more explicit `keymaster_listener.py` name without changing scope.
- **FR-005**: The integration entry module MUST remain responsible for high-level orchestration of setup, unload, update-listener handling, and package-listener startup.
- **FR-006**: Public integration entry points relied on by Home Assistant, other modules, or tests MUST remain importable and callable from their existing package-level locations; package-level re-exports MUST be used where necessary to preserve that contract.
- **FR-007**: Listener lifecycle behavior MUST remain unchanged: listeners are added only on the existing setup or update paths, unsubscribe callbacks are tracked with the entry data, and cleanup removes the same callbacks during refresh or unload.
- **FR-008**: Keymaster event acceptance and rejection MUST remain unchanged for monitored lock names, unlock state, nonzero slot numbers, slot range, check-in sensor availability, monitoring switch availability, monitoring switch state, and diagnostic recording.
- **FR-009**: `custom_components/rental_control/__init__.py` MUST be under the 400-line file-size threshold after decomposition.
- **FR-010**: No function in the decomposed in-scope areas MUST exceed the 80-line function-length threshold after decomposition.
- **FR-011**: Existing behavior assertions in the test suite MUST pass unchanged; implementation-stage test changes, if any, MUST only account for moved module boundaries or additional coverage, not changed runtime behavior.
- **FR-012**: The refactor MUST NOT introduce new user-facing configuration, entity state, service behavior, migration semantics, listener semantics, diagnostic semantics, or unrelated complexity cleanup.

### Key Entities

- **Integration Entry Module**: The package entry area that Home Assistant uses for setup, unload, migration, update-listener, and listener-start orchestration. Its responsibility after the refactor is coordination rather than detailed migration or keymaster event processing.
- **Migration Flow**: The ordered set of configuration version transitions that keeps existing entries usable after upgrades. It must remain behaviorally identical while becoming independently reviewable.
- **Keymaster Listener Flow**: The registration and event-filtering responsibility that receives keymaster lock-state events, rejects non-matching events, records diagnostics when enabled, and forwards accepted unlocks to the check-in sensor.
- **Public Entry Point**: Any package-level function name that Home Assistant, tests, or project modules already rely on, including setup, unload, migration, update-listener, package-listener startup, and keymaster-listener registration names.
- **Complexity Threshold**: The maintainability limit reported by issue #572: the integration entry module must be below 400 lines and in-scope functions must be at or below 80 lines.

## Assumptions

- This is a pure internal refactor; external and runtime behavior must remain unchanged.
- The in-scope functions are the issue-reported `async_migrate_entry` and `async_register_keymaster_listener`, plus only the imports, re-exports, and call sites needed to move those responsibilities safely.
- The top-level functions currently present in the integration entry module are `async_setup_entry`, `async_unload_entry`, `async_migrate_entry`, `update_listener`, `async_start_listener`, and `async_register_keymaster_listener`; their existing package-level contracts remain relevant.
- The default module names for planning are `migrations.py` for migration logic and `listeners.py` for keymaster listener logic; selecting `keymaster_listener.py` during planning is acceptable only if it preserves the same scope and contracts.
- Existing tests are the behavioral baseline for this refactor. Any implementation-stage test updates should strengthen coverage or adjust import locations without changing expected behavior.
- Changing migration semantics, keymaster event semantics, user-facing options, entity behavior, generated package behavior, or unrelated complexity warnings is out of scope.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `custom_components/rental_control/__init__.py` contains fewer than 400 lines after the decomposition.
- **SC-002**: 100% of functions in the decomposed in-scope areas are at or below 80 lines after the decomposition.
- **SC-003**: 100% of existing behavior tests pass with unchanged assertions for setup, unload, migration, listener lifecycle, keymaster diagnostics, and check-in forwarding behavior.
- **SC-004**: 100% of existing package-level public entry point imports used by Home Assistant, project modules, or tests remain valid after the decomposition.
- **SC-005**: The detailed migration flow and the detailed keymaster listener flow each reside outside the integration entry module in a dedicated review area, so either responsibility can be inspected without reading unrelated setup or unload orchestration.
- **SC-006**: The refactor introduces zero new user-facing options, entity states, service behaviors, migration semantics, listener semantics, or diagnostic semantics.
