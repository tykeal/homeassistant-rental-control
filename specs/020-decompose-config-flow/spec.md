<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Feature Specification: Decompose Config Flow

**Feature Branch**: `020-decompose-config-flow`
**Created**: 2026-06-29
**Status**: Draft
**Input**: User description: "Decompose
`custom_components/rental_control/config_flow.py` for GitHub issue #573.
This is a behavior-preserving code-health refactor of the oversized Rental
Control configuration flow. Extract schema construction and validation or
step-transition helpers while keeping the Home Assistant flow classes as the
HA-facing shells."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Preserve Initial Configuration Flow (Priority: P1)

As a property manager adding a Rental Control calendar, I want the initial
configuration flow to present the same fields, defaults, validation, and entry
creation behavior after decomposition, so upgrades do not change how calendars
are added or validated.

**Why this priority**: The initial Home Assistant config flow validates the
calendar URL, times, refresh frequency, lock selection, code-generation choices,
trim-name limits, duplicate detection, and creation metadata before a calendar
can be used for rental automation.

**Independent Test**: Can be fully tested by running existing config-flow tests
unchanged and by comparing the form schema, default values, selector options,
validation errors, duplicate handling, URL fetch behavior, and created entry data
for identical initial-flow inputs before and after decomposition.

**Acceptance Scenarios**:

1. **Given** a user starts the Rental Control integration setup, **When** the
   decomposed flow renders the first form, **Then** step `user`, required fields,
   optional fields, defaults, selectors, and empty errors match the current
   implementation.
2. **Given** a valid calendar URL and valid form values, **When** the user
   submits the initial flow, **Then** the same calendar fetch, content-type
   validation, lock-entry conversion, code-generator conversion, creation
   timestamp, generated flag, title, and entry data are produced.
3. **Given** the calendar URL is malformed, unsupported, non-HTTPS while SSL
   verification is enabled, non-200, or not `text/calendar`, **When** the user
   submits the form, **Then** the same field errors are returned as today.
4. **Given** refresh frequency, check-in time, checkout time, day count, maximum
   events, code length, maximum name length, trim-name prefix boundary, or unique
   ID validation fails, **When** the form is submitted, **Then** the same error
   keys and re-rendered schema defaults are returned.
5. **Given** tests patch UUID generation through the current config-flow module,
   **When** duplicate detection runs, **Then** the patch remains effective and the
   same duplicate error behavior is observed.

---

### User Story 2 - Preserve Options Flow Behavior (Priority: P1)

As an existing Rental Control user editing options, I want the options flow to
load existing configuration, show the same editable fields, and save the same
validated data, so decomposition does not change existing integrations.

**Why this priority**: Options editing is how users change refresh, time,
lock-entry, diagnostics, code buffer, trim-name, SSL, and code-generation
settings after setup. Behavior drift can affect future lock-code generation or
calendar refresh behavior.

**Independent Test**: Can be fully tested by existing options-flow tests
unchanged and by comparing option-form schemas, defaults loaded from entry data,
validation errors, diagnostics fields, buffer fields, lock-entry conversion, and
saved entry data for identical option inputs before and after decomposition.

**Acceptance Scenarios**:

1. **Given** an existing config entry has stored data, **When** the options flow
   starts, **Then** step `init` loads the same defaults from the entry and renders
   the same base and options-only fields.
2. **Given** the stored lock entry is `None` or a lock entity ID, **When** the
   options schema is built, **Then** it displays the same `(none)` sentinel or
   lock-manager title and accepts the same user selections.
3. **Given** valid option values are submitted, **When** the flow completes,
   **Then** the same conversions, diagnostics values, code-buffer values, and
   updated entry data are saved.
4. **Given** invalid URL, time, numeric, code-length, trim-name, or lock-entry
   inputs are submitted, **When** the options form is re-rendered, **Then** the
   same errors, entered values, and description placeholders are preserved.

---

### User Story 3 - Preserve Config Flow Compatibility Surface (Priority: P1)

As a Rental Control maintainer, I want the Home Assistant flow classes and
current config-flow module seams to remain importable and behavior-compatible, so
this refactor can be reviewed as a behavior-preserving decomposition rather than
a coordinated caller or test rewrite.

**Why this priority**: Home Assistant discovers and drives the flow through
`RentalControlFlowHandler`, `RentalControlOptionsFlow`, `async_step_user`,
`async_step_init`, `async_get_options_flow`, and `VERSION`. Visible tests also
import `_normalize_lock_entry`, import `RentalControlFlowHandler` for `VERSION`,
and patch `custom_components.rental_control.config_flow.gen_uuid`; hidden tests
may call current helper seams directly.

**Independent Test**: Can be fully tested by running existing tests unchanged,
verifying Home Assistant can initialize the config and options flows, and
checking that currently importable flow classes, steps, version metadata,
callbacks, and helper seams remain importable from
`custom_components.rental_control.config_flow` with compatible behavior.

**Acceptance Scenarios**:

1. **Given** Home Assistant imports the integration, **When** config flow setup is
   registered, **Then** `RentalControlFlowHandler` remains a `ConfigFlow` shell
   with `VERSION = 10`, `async_step_user`, the `@callback`-decorated
   `async_get_options_flow`, and the same domain registration behavior.
2. **Given** Home Assistant starts options editing, **When** the options flow is
   created, **Then** `RentalControlOptionsFlow` remains an `OptionsFlow` shell
   with `async_step_init` and the same config-entry data loading behavior.
3. **Given** visible tests import `_normalize_lock_entry` or
   `RentalControlFlowHandler`, **When** the decomposed module loads, **Then**
   those imports and behaviors remain unchanged.
4. **Given** visible tests patch `custom_components.rental_control.config_flow.gen_uuid`,
   **When** unique ID generation is exercised, **Then** that monkeypatch seam
   remains effective without test rewrites.
5. **Given** current or hidden tests import `_get_schema`, `_show_config_form`, or
   `_start_config_flow` from `config_flow.py`, **When** the decomposed module
   loads, **Then** those helpers remain importable and behavior-compatible from
   that path unless a later accepted change explicitly removes the compatibility
   surface.

---

### User Story 4 - Improve Maintainability Under Aislop Limits (Priority: P2)

As a maintainer, I want the configuration flow split into focused schema and
validation or transition concerns, so future flow changes can target the relevant
behavior without navigating an oversized module with active complexity findings.

**Why this priority**: Issue #573 identifies `config_flow.py` as above the
400-line file threshold, with `_get_schema` and `_start_config_flow` above the
80-line function threshold. The live file also has an over-parameter
`_show_config_form` helper. The existing `ai-slop/hallucinated-import`
directive is required for Home Assistant runtime imports, but there is no
complexity directive and the complexity findings must be resolved rather than
suppressed.

**Independent Test**: Can be fully tested by measuring the decomposed
configuration-flow feature area against active complexity thresholds while
existing config-flow behavior tests continue to pass unchanged.

**Acceptance Scenarios**:

1. **Given** the decomposition is complete, **When** complexity checks run,
   **Then** config-flow-related files are below 400 lines, project-owned
   functions are below 80 lines, and project-owned parameter lists have no more
   than 6 parameters unless an external framework signature requires otherwise.
2. **Given** the Home Assistant classes remain in `config_flow.py`, **When** the
   code is inspected, **Then** they are shells around focused schema,
   validation, and transition helpers rather than owners of all schema and
   validation details.
3. **Given** the existing `# aislop-ignore-file ai-slop/hallucinated-import`
   directive is required for Home Assistant runtime imports, **When** the file is
   decomposed, **Then** that directive remains present and no new complexity
   ignore or suppression directive is added.
4. **Given** planning and implementation decide exact helper boundaries, **When**
   work is scoped, **Then** the split is by coherent concern and does not depend
   on exact module names prescribed by this specification.

---

### Edge Cases

- What happens when the Home Assistant frontend clears the lock-entry selector to
  `None`, an empty string, or whitespace? `_normalize_lock_entry` keeps mapping
  those values to `(none)`, and `(none)` still stores as `None` only after
  validation succeeds.
- What happens when defaults contain no lock entry, `None`, `(none)`, a lock
  manager title, or a lock entity ID? The schema displays and accepts the same
  lock choices and conversion results as today.
- What happens when the URL is valid but SSL verification is enabled for an HTTP
  URL, the response is not 200, the fetch times out, or the content type is not a
  calendar? The same URL validation and error mapping is preserved.
- What happens when code-generator descriptions are shown to users and stored
  types are saved internally? The same description-to-type and type-to-description
  conversions remain the source of truth.
- What happens when trim-name is enabled and the event prefix leaves too little
  room for the minimum guest name length? The same base error prevents saving.
- What happens when validation errors occur after user input has been normalized
  or converted? The form re-rendering preserves the same entered values, schema
  defaults, and later successful conversion behavior as today.
- What happens when current or hidden tests import or patch config-flow module
  helpers? The current compatibility paths remain available and effective.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The decomposition MUST preserve all Home Assistant observable
  behavior of `custom_components/rental_control/config_flow.py`, including flow
  registration, form rendering, schema contents, defaults, selector options,
  validation errors, description placeholders, URL fetch behavior, entry titles,
  entry data, and compatibility imports.
- **FR-002**: Existing config-flow tests MUST pass unchanged after the
  implementation stage; new tests MUST verify behavior parity or focused
  extracted behavior rather than introduce new runtime behavior.
- **FR-003**: The Home Assistant-facing public surface MUST remain in
  `config_flow.py`: `RentalControlFlowHandler` as the `ConfigFlow` subclass,
  `RentalControlOptionsFlow` as the `OptionsFlow` subclass, `VERSION = 10`,
  `async_step_user`, `async_step_init`, `async_get_options_flow`, and the
  `@callback` decoration and domain registration behavior required by Home
  Assistant.
- **FR-004**: The current test-consumed compatibility surface MUST remain
  importable and behavior-compatible from
  `custom_components.rental_control.config_flow`, including
  `RentalControlFlowHandler`, `_normalize_lock_entry`, and the module-level
  `gen_uuid` monkeypatch seam. Current or hidden helper consumers of
  `_get_schema`, `_show_config_form`, or `_start_config_flow` MUST remain
  compatible unless a later accepted change explicitly narrows that surface.
- **FR-005**: `_get_schema` behavior MUST remain equivalent for all current
  schema keys, required and optional field status, validators, `ALLOW_EXTRA`,
  default values, timezone options, lock-manager selector options,
  code-generator choices, options-only diagnostics and buffer fields, and
  lock-entry default conversion from stored entity IDs to displayed titles.
- **FR-006**: Schema construction MUST be decomposed into focused per-step or
  per-concern schema builders so the schema behavior remains identical while the
  project-owned function-length threshold is satisfied.
- **FR-007**: `_start_config_flow` behavior MUST remain equivalent for
  user-input normalization, duplicate detection, URL validation, SSL handling,
  calendar fetch and content-type checks, time validation, numeric bounds,
  code-length rules, code-generator conversion, trim-name prefix validation,
  lock-entry conversion, creation timestamp insertion, generated flag insertion,
  entry creation, and form re-rendering on errors.
- **FR-008**: Validation and step-transition behavior MUST be decomposed into
  focused helpers so the project-owned function-length threshold is satisfied
  without changing error precedence, mutation timing, logging behavior relied on
  by tests, or returned flow results.
- **FR-009**: `_show_config_form` MUST be brought to no more than 6 parameters by
  grouping form context, defaults, entry ID, or equivalent request data without
  changing the returned form, schema, errors, or description placeholders.
- **FR-010**: Initial config-flow and options-flow step IDs MUST remain `user`
  and `init`, respectively, and Home Assistant config and options flow
  registration MUST remain unchanged.
- **FR-011**: The completed decomposition MUST keep config-flow-related files
  below 400 lines, project-owned functions below 80 lines, and project-owned
  parameter lists at no more than 6 parameters unless an external framework
  signature requires otherwise.
- **FR-012**: The existing
  `# aislop-ignore-file ai-slop/hallucinated-import -- Provided by Home Assistant runtime.`
  directive MUST remain present for Home Assistant runtime imports.
- **FR-013**: The implementation MUST NOT add any new Aislop ignore or
  suppression directive, including `aislop-ignore`, `aislop-ignore-file`, or
  equivalent directives in `config_flow.py` or replacement modules, to suppress
  file-size, function-length, or parameter-count findings. Complexity findings
  must be resolved by behavior-preserving decomposition.
- **FR-014**: Planning and implementation documentation MUST state that this is a
  behavior-preserving refactor and MUST NOT define new configuration options, new
  flow steps, new validation rules, changed error keys, changed defaults, changed
  selector choices, changed entry data, or changed public caller behavior.

### Key Entities

- **Config Flow Shell**: The Home Assistant-facing `RentalControlFlowHandler`
  class that remains in `config_flow.py`, owns `VERSION = 10`, handles
  `async_step_user`, exposes `async_get_options_flow`, and delegates detailed
  schema and validation work to focused helpers.
- **Options Flow Shell**: The Home Assistant-facing `RentalControlOptionsFlow`
  class that remains in `config_flow.py`, owns `async_step_init`, loads existing
  config-entry data, and delegates detailed schema and validation work.
- **Schema Definition**: The complete set of current config and options fields,
  validators, defaults, selectors, timezone options, lock-manager choices,
  diagnostics fields, buffer fields, and `ALLOW_EXTRA` behavior produced for each
  form render.
- **Flow Validation Result**: The accumulated field and base errors, normalized
  user input, converted lock-entry and code-generator values, and transition
  decision used to either re-render the form or create an entry.
- **Form Display Context**: A grouped value or equivalent mechanism used to keep
  `_show_config_form` at or below 6 parameters while preserving the same form
  result.
- **Config Flow Compatibility Surface**: The names importable from
  `custom_components.rental_control.config_flow` that Home Assistant, visible
  tests, and hidden tests may consume, including the flow classes, HA step
  methods, `async_get_options_flow`, `_normalize_lock_entry`, `_get_schema`,
  `_show_config_form`, `_start_config_flow`, and the `gen_uuid` patch seam.

## Assumptions

- This specification covers issue #573's spec stage only; planning and
  implementation stages will decide exact helper boundaries, module layout,
  dataclass or context shape, and compatibility mechanics.
- The live source read for this specification is a 549-line
  `custom_components/rental_control/config_flow.py`, above the active 400-line
  file threshold.
- The active function-length findings are `_get_schema` at 151 lines and
  `_start_config_flow` at 147 lines. The active parameter-count finding is
  `_show_config_form` with seven parameters.
- The only current Aislop directive in `config_flow.py` is
  `# aislop-ignore-file ai-slop/hallucinated-import -- Provided by Home Assistant runtime.`;
  no complexity directive exists today.
- Current Home Assistant consumption is through `RentalControlFlowHandler`,
  `RentalControlOptionsFlow`, `async_step_user`, `async_step_init`,
  `async_get_options_flow`, `VERSION`, and domain registration. Visible tests use
  Home Assistant flow managers, directly import `_normalize_lock_entry`, import
  `RentalControlFlowHandler` to assert `VERSION`, and patch
  `custom_components.rental_control.config_flow.gen_uuid`.
- Current visible tests do not directly import `_get_schema`, `_show_config_form`,
  or `_start_config_flow`, but those helper paths are currently importable and may
  be used by hidden tests or downstream regression checks.
- Runtime performance expectations are parity with the current implementation in
  normal Home Assistant operation, not a new user-visible performance feature.

## Non-Goals

- Changing Home Assistant-visible config or options flow behavior, UX, step IDs,
  fields, labels, defaults, selectors, validation rules, validation error keys,
  entry titles, or entry data.
- Adding new configuration options, options-only fields, flow steps, diagnostics
  fields, services, sensors, automations, Store authority, or recovery workflows.
- Changing URL fetch policy, SSL verification behavior, calendar content-type
  requirements, duplicate detection, lock-entry conversion, code-generator
  conversion, trim-name validation, or code-length validation.
- Changing the public config-flow import and monkeypatch surfaces consumed by
  current production callers, visible tests, or hidden tests.
- Prescribing exact file names, helper module names, class names, dataclass field
  names, or helper signatures for the plan and implementation stages.
- Adding any Aislop ignore or suppression directive for config-flow complexity
  findings.
- Closing issue #573 in this specification PR; later implementation work owns the
  runtime refactor.

## Constraints

- No behavior observable by Home Assistant users, dashboards, automations,
  services, logs relied on by tests, physical Keymaster state, stored config-entry
  data, options data, or existing tests may change as part of this refactor.
- `RentalControlFlowHandler` and `RentalControlOptionsFlow` MUST remain the
  Home Assistant-facing flow classes in `config_flow.py`.
- `async_step_user`, `async_step_init`, `async_get_options_flow`, `VERSION`, the
  `@callback` decorator, and domain registration MUST satisfy the Home Assistant
  config-flow and options-flow contracts exactly as today.
- Existing test import, direct-call, and monkeypatch boundaries MUST remain
  compatible, especially `_normalize_lock_entry`, `RentalControlFlowHandler`, and
  `custom_components.rental_control.config_flow.gen_uuid`.
- The existing hallucinated-import Aislop directive MUST stay, and the final
  implementation MUST satisfy active file-size, function-length, and
  parameter-count thresholds without adding suppressing directives.
- This specification stage is documentation-only and MUST NOT include production
  code changes.

## Security Considerations

- The configuration flow controls calendar URLs, SSL verification, lock-manager
  association, lock-code generation mode, slot ranges, code length, code buffers,
  and trim-name behavior. Behavior drift can cause unsafe lock-code programming,
  stale access windows, or insecure calendar fetching.
- Lock-entry conversion must remain conservative: cleared selections continue to
  store as no lock, and selected lock-manager entries continue to resolve to the
  same underlying lock entity only after validation succeeds.
- URL validation and SSL handling must continue to reject insecure or malformed
  inputs in the same cases as today, because these settings control calendar data
  ingestion for access automation.
- Logs, schema defaults, and helper boundaries must continue to expose no more
  sensitive calendar or lock information than existing Rental Control behavior
  already exposes.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of existing config-flow unit tests pass unchanged after the
  implementation stage completes, including initial flow, options flow,
  validation, duplicate detection, lock-entry normalization, diagnostics, code
  buffers, trim-name, and version coverage.
- **SC-002**: In 100% of covered config and options form-render scenarios, schema
  keys, required and optional status, validators, defaults, selector options,
  timezone choices, diagnostics fields, buffer fields, `ALLOW_EXTRA`, errors, and
  description placeholders match the current implementation.
- **SC-003**: In 100% of covered validation scenarios, URL errors, SSL handling,
  HTTP response handling, content-type handling, time errors, numeric bound
  errors, code-length errors, trim-name prefix errors, duplicate errors, and
  successful entry data match the current implementation.
- **SC-004**: Home Assistant can initialize the initial config flow and options
  flow through the same domain, step IDs, `ConfigFlow` subclass,
  `OptionsFlow` subclass, `VERSION`, `async_step_user`, `async_step_init`, and
  `async_get_options_flow` surfaces without caller changes.
- **SC-005**: All visible tests that import `_normalize_lock_entry` or
  `RentalControlFlowHandler`, patch `custom_components.rental_control.config_flow.gen_uuid`,
  or use Home Assistant flow managers continue to do so without behavior changes
  or behavior-assertion rewrites.
- **SC-006**: The decomposed config-flow feature area contains no files of 400
  lines or more, no project-owned functions of 80 lines or more, and no
  project-owned parameter lists over 6 parameters.
- **SC-007**: Active complexity checks pass without adding any config-flow Aislop
  ignore or suppression directive for file size, function length, or parameter
  count, while the existing `ai-slop/hallucinated-import` directive remains.
- **SC-008**: Normal config and options flow processing performs no additional
  Home Assistant state writes, config-entry mutations, calendar fetches,
  Keymaster service calls, blocking I/O, async tasks, or user-visible delays
  compared with the current implementation.
- **SC-009**: No production-code changes are included in this specification PR;
  the PR contains only the feature specification for the #573 decomposition
  pipeline.
