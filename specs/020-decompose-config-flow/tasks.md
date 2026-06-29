<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Tasks: Decompose Config Flow

**Input**: Design documents from `/specs/020-decompose-config-flow/`
**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅,
quickstart.md ✅

**Tests**: Included and required. This feature is a behavior-preserving
refactor of `custom_components/rental_control/config_flow.py`, so existing
config-flow tests remain the primary oracle. New focused tests pin schema
construction, validation/conversion helpers, transition orchestration,
`ConfigFormContext` form rendering, and import/patch seams.

**Organization**: Tasks follow the implementation order from PLAN and the
feature brief: baseline parity first, helper models and `ConfigFormContext`,
schema builders, validation/conversion helpers, step helpers, shell delegation
with `_get_schema` and `_start_config_flow` split, `_show_config_form`
parameter reduction at both internal call sites, import and patch-seam
verification, maintainability gates, and final pytest/ruff/pre-commit/Aislop
acceptance. All checkboxes remain unchecked until the implementation PR
performs the work.

## Format: `- [ ] T### [P?] [Story?] Description with file path`

- **[P]**: Can run in parallel (different files, no dependency on incomplete
  tasks)
- **[Story]**: Which user story the task primarily proves (US1 through US4)
- Include exact file paths in descriptions
- Leave every checkbox unchecked until the implementation PR performs the task

## Path Conventions

- **Public shell**: `custom_components/rental_control/config_flow.py`
- **Extracted helper package**:
  `custom_components/rental_control/config_flow_helpers/`
- **Helper modules**:
  `custom_components/rental_control/config_flow_helpers/models.py`,
  `schemas.py`, `validation.py`, and `steps.py`
- **Required config-flow compatibility surface**: `RentalControlFlowHandler`,
  `RentalControlOptionsFlow`, `gen_uuid`, `_normalize_lock_entry`,
  `_get_schema`, `_show_config_form`, and `_start_config_flow`
- **Patch-sensitive config-flow attribute**: `gen_uuid` must remain patchable at
  `custom_components.rental_control.config_flow.gen_uuid` through call-time
  lookup by `RentalControlFlowHandler._get_unique_id()`
- **Existing behavior-oracle tests**: `tests/unit/test_config_flow.py`,
  `tests/integration/test_full_setup.py`, and
  `tests/integration/test_refresh_cycle.py`
- **New focused tests**: `tests/unit/test_config_flow_schemas.py` and
  `tests/unit/test_config_flow_validation.py`
- **Feature docs**: `specs/020-decompose-config-flow/`

## Live Module Transition Scope

Implementation changes the config-flow feature only. The target split from
PLAN is:

- `custom_components/rental_control/config_flow.py` — public Home Assistant
  config-flow shell, `RentalControlFlowHandler`, `RentalControlOptionsFlow`,
  `VERSION = 10`, `async_step_user`, `async_step_init`,
  `@callback async_get_options_flow`, module-level `gen_uuid`, and compatibility
  wrappers for `_normalize_lock_entry`, `_get_schema`, `_show_config_form`, and
  `_start_config_flow`.
- `custom_components/rental_control/config_flow_helpers/models.py` — internal
  typed containers for `ConfigFormContext`, schema-build context, URL validation
  result, flow validation result, and step transition request data.
- `custom_components/rental_control/config_flow_helpers/schemas.py` — focused
  schema/default/selector builders that preserve `_get_schema` output exactly.
- `custom_components/rental_control/config_flow_helpers/validation.py` — URL,
  scalar, code-generator, trim-name, lock-entry, and successful-conversion
  helpers with unchanged error precedence and mutation timing.
- `custom_components/rental_control/config_flow_helpers/steps.py` — short
  submitted-data orchestration and form/create-entry transition helpers.

No implementation task may add `aislop-ignore`, `aislop-ignore-file`, or an
equivalent complexity suppression for file size, function length, or parameter
count. The existing
`# aislop-ignore-file ai-slop/hallucinated-import -- Provided by Home Assistant runtime.`
directive must remain in `config_flow.py`.

---

## Phase 1: Setup & Baseline (Shared Infrastructure)

**Purpose**: Establish behavior, import, patch-site, caller, and complexity
baselines before moving any production code.

- [ ] T001 Run `.specify/scripts/bash/check-prerequisites.sh --json` with `SPECIFY_FEATURE=020-decompose-config-flow` from the repository root and confirm `specs/020-decompose-config-flow/` reports `research.md`, `data-model.md`, and `quickstart.md`
- [ ] T002 Inspect US1-US4, FR-001 through FR-014, edge cases, assumptions, non-goals, security considerations, and SC-001 through SC-009 in `specs/020-decompose-config-flow/spec.md`
- [ ] T003 Inspect the Project Structure, Concrete Decomposition Design, compatibility boundary, ground-truth call and patch analysis, helper split, `_show_config_form` reduction, and Aislop directive handling in `specs/020-decompose-config-flow/plan.md`
- [ ] T004 Inspect all research decisions, all data-model helper entities, and quickstart parity steps in `specs/020-decompose-config-flow/research.md`, `specs/020-decompose-config-flow/data-model.md`, and `specs/020-decompose-config-flow/quickstart.md`
- [ ] T005 Inventory `RentalControlFlowHandler`, `RentalControlOptionsFlow`, `async_step_user`, `async_step_init`, `async_get_options_flow`, `_get_unique_id`, `_normalize_lock_entry`, `_get_schema`, `_show_config_form`, and `_start_config_flow` in `custom_components/rental_control/config_flow.py`
- [ ] T006 Inventory visible config-flow imports, `custom_components.rental_control.config_flow.gen_uuid` patches, `_normalize_lock_entry` direct calls, Home Assistant flow-manager calls, and `RentalControlFlowHandler.VERSION == 10` assertions in `tests/unit/test_config_flow.py`
- [ ] T007 Run unchanged baseline config-flow unit tests with `uv run pytest tests/unit/test_config_flow.py -q` against `tests/unit/test_config_flow.py`
- [ ] T008 Run unchanged integration caller smoke tests with `uv run pytest tests/integration/test_full_setup.py tests/integration/test_refresh_cycle.py -q` against the listed integration files
- [ ] T009 Record the current line, function-length, parameter-count, call-site, and directive baseline for `custom_components/rental_control/config_flow.py`, confirming the oversized file, long `_get_schema`, long `_start_config_flow`, seven-parameter `_show_config_form`, call sites around the current error and initial-form paths, presence of the hallucinated-import directive, and absence of any config-flow complexity directive

---

## Phase 2: Helper Models and `ConfigFormContext` (Priority: P1)

**Goal**: Introduce internal typed values that make form rendering, schema
construction, validation, and step orchestration explicit while keeping the
public config-flow shell unchanged.

**Independent Test**: Construct each helper model with representative config and
options values and verify fields carry the same step IDs, defaults, submitted
input, errors, placeholders, entry IDs, validation state, and transition data
used by the current flow.

### Tests for Helper Models

- [ ] T010 [US4] Add `ConfigFormContext`, schema-build context, URL validation result, flow validation result, and transition request construction tests in `tests/unit/test_config_flow_schemas.py`
- [ ] T011 [US1] Add model tests proving error dictionaries, description placeholders, submitted `user_input`, defaults, and `entry_id` are preserved for both config and options form renders in `tests/unit/test_config_flow_schemas.py`

### Implementation for Helper Models

- [ ] T012 [US4] Create `custom_components/rental_control/config_flow_helpers/__init__.py` with SPDX headers, a module docstring, and internal helper-package exports as needed
- [ ] T013 [US4] Create `custom_components/rental_control/config_flow_helpers/models.py` with SPDX headers, a module docstring, type hints, `ConfigFormContext`, schema-build context, URL validation result, flow validation result, and transition request dataclasses or equivalent typed containers
- [ ] T014 [US4] Ensure `custom_components/rental_control/config_flow_helpers/models.py` imports no runtime objects from `custom_components/rental_control/config_flow.py` and remains below 400 lines with no project-owned function over 80 lines or parameter list over six parameters
- [ ] T015 [US4] Run model validation with `uv run pytest tests/unit/test_config_flow_schemas.py -q` against `tests/unit/test_config_flow_schemas.py`

**Checkpoint**: Helper models prove the data ownership needed for FR-009,
FR-011, SC-006, and SC-007 before extraction begins.

---

## Phase 3: Schema Builder Extraction (Priority: P1)

**Goal**: Preserve `_get_schema` behavior while moving schema construction into
focused per-concern builders.

**Independent Test**: Compare schemas produced for initial config and options
flows against current required/optional keys, defaults, selectors, lock-manager
choices, code-generator descriptions, timezone options, validators, options-only
fields, and `ALLOW_EXTRA` behavior.

### Tests for Schema Builders

- [ ] T016 [P] [US1] Add initial config-flow schema parity tests for required fields, optional fields, default values, timezone choices, code-generator descriptions, lock selector contents, and `ALLOW_EXTRA` in `tests/unit/test_config_flow_schemas.py`
- [ ] T017 [P] [US2] Add options-flow schema parity tests for defaults loaded from config-entry data, options-only diagnostics and code-buffer fields, `(none)` lock defaults, stored lock entity display conversion, and entered-value precedence in `tests/unit/test_config_flow_schemas.py`
- [ ] T018 [US1] Add `_get_schema` compatibility tests proving `custom_components.rental_control.config_flow._get_schema` remains importable and returns equivalent schemas for config and options contexts in `tests/unit/test_config_flow_schemas.py`

### Implementation for Schema Builders

- [ ] T019 [US4] Create `custom_components/rental_control/config_flow_helpers/schemas.py` with SPDX headers, a module docstring, and focused builders for defaults normalization, default lookup, identity/URL fields, refresh/timezone/prefix/time fields, day and lock fields, slot/code/generator fields, behavior toggles, trim-name fields, and options-only fields
- [ ] T020 [US1] Move `_available_lock_managers`, `_code_generators`, `_generator_convert` display conversion, and `_lock_entry_convert` display conversion behavior into schema helpers or shared validation helpers without changing selector order, labels, defaults, or returned values in `custom_components/rental_control/config_flow_helpers/schemas.py`
- [ ] T021 [US1] Update `custom_components/rental_control/config_flow.py` so `_get_schema` remains importable as a thin wrapper over `custom_components/rental_control/config_flow_helpers/schemas.py` with the current `hass`, `user_input`, `default_dict`, and `entry_id` call semantics
- [ ] T022 [US2] Preserve options-only extension behavior for `CONF_ENABLE_KEYMASTER_EVENT_DIAGNOSTICS`, `CONF_CODE_BUFFER_BEFORE`, and `CONF_CODE_BUFFER_AFTER` when `entry_id` is not `None` in `custom_components/rental_control/config_flow_helpers/schemas.py`
- [ ] T023 [US1] Run schema validation with `uv run pytest tests/unit/test_config_flow.py tests/unit/test_config_flow_schemas.py -q` against the listed test files

**Checkpoint**: Schema extraction proves FR-001, FR-002, FR-005, FR-006,
FR-010, FR-014, SC-001, SC-002, SC-004, and SC-005 before validation moves.

---

## Phase 4: Validation and Conversion Helpers (Priority: P1)

**Goal**: Preserve `_start_config_flow` validation behavior while separating URL,
scalar, code-generator, trim-name, lock-entry, and successful conversion
concerns.

**Independent Test**: Submit identical user inputs to the flow and focused
helpers and verify URL errors, SSL behavior, HTTP status handling, content-type
handling, scalar errors, code-generator mutation timing, trim-name base errors,
lock-entry conversions, creation metadata, generated flags, and saved data match
current behavior.

### Tests for Validation Helpers

- [ ] T024 [P] [US1] Add URL validation parity tests for malformed URL, unsupported scheme, HTTPS-required, SSL-disabled HTTP, non-200 response, timeout path if covered by existing helpers, and non-calendar content type in `tests/unit/test_config_flow_validation.py`
- [ ] T025 [P] [US1] Add scalar validation parity tests for refresh frequency bounds, check-in time, checkout time, day count, max events, code length minimum/evenness, max-name length, and trim-name prefix boundary errors in `tests/unit/test_config_flow_validation.py`
- [ ] T026 [US1] Add code-generator conversion timing tests proving display descriptions convert to generator types before form re-render on later validation errors in `tests/unit/test_config_flow_validation.py`
- [ ] T027 [US2] Add lock-entry conversion tests for `None`, empty string, whitespace, `(none)`, lock-manager title, stored lock entity ID defaults, and successful options-flow save data in `tests/unit/test_config_flow_validation.py`
- [ ] T028 [US1] Add successful initial-flow conversion tests proving `CONF_CREATION_DATETIME` and `CONF_GENERATE` insertion match current behavior in `tests/unit/test_config_flow_validation.py`

### Implementation for Validation Helpers

- [ ] T029 [US4] Create `custom_components/rental_control/config_flow_helpers/validation.py` with SPDX headers, a module docstring, and focused helpers for lock normalization, URL validation/fetch, scalar checks, code-generator conversion, trim-name checks, lock conversion, and successful metadata insertion
- [ ] T030 [US3] Keep `custom_components.rental_control.config_flow._normalize_lock_entry` importable as a wrapper or re-export while preserving exact behavior for `None`, empty strings, whitespace, and existing values in `custom_components/rental_control/config_flow.py`
- [ ] T031 [US1] Preserve URL validation and fetch behavior in `custom_components/rental_control/config_flow_helpers/validation.py`, including `cv.url()`, HTTPS-required with `CONF_VERIFY_SSL`, `async_get_clientsession(..., verify_ssl=...)`, `asyncio.timeout(REQUEST_TIMEOUT)`, non-200 logging, and `text/calendar` content-type checks
- [ ] T032 [US1] Preserve scalar validation error keys and precedence in `custom_components/rental_control/config_flow_helpers/validation.py` for refresh frequency, check-in, checkout, days, max events, code length, max-name length, and trim-name prefix boundary
- [ ] T033 [US1] Preserve code-generator conversion and mutation timing in `custom_components/rental_control/config_flow_helpers/validation.py` so re-rendered forms observe the same converted value behavior as current `_start_config_flow`
- [ ] T034 [US2] Preserve successful lock-entry conversion in `custom_components/rental_control/config_flow_helpers/validation.py`, converting `(none)` to `None` and lock-manager titles to entity IDs only when no errors exist
- [ ] T035 [US1] Run validation helper checks with `uv run pytest tests/unit/test_config_flow.py tests/unit/test_config_flow_validation.py -q` against the listed test files

**Checkpoint**: Validation helpers prove FR-001, FR-002, FR-004, FR-007,
FR-008, FR-010, FR-014, SC-001, SC-003, SC-005, and SC-008.

---

## Phase 5: Step Transition Helpers (Priority: P1)

**Goal**: Move submitted-data orchestration out of the shell while keeping
`_start_config_flow` importable and behavior-compatible.

**Independent Test**: Exercise initial renders, error re-renders, successful
initial entry creation, successful options updates, duplicate detection, and
form contexts while confirming the same step IDs, titles, errors, placeholders,
defaults, and entry data are returned.

### Tests for Step Helpers

- [ ] T036 [US1] Add step-transition tests proving an initial `user_input is None` flow returns the same `user` form with empty errors and current defaults in `tests/unit/test_config_flow_validation.py`
- [ ] T037 [US1] Add error re-render tests proving submitted `user_input`, accumulated field/base errors, description placeholders, defaults, and `entry_id` are preserved in `tests/unit/test_config_flow_validation.py`
- [ ] T038 [US1] Add create-entry transition tests proving initial-flow title, data, duplicate-detection behavior, creation timestamp insertion, and generated flag insertion remain unchanged in `tests/unit/test_config_flow_validation.py`
- [ ] T039 [US2] Add options-flow transition tests proving step `init`, existing entry defaults, options-only fields, saved data, and returned create-entry result remain unchanged in `tests/unit/test_config_flow_validation.py`

### Implementation for Step Helpers

- [ ] T040 [US4] Create `custom_components/rental_control/config_flow_helpers/steps.py` with SPDX headers, a module docstring, and short orchestration helpers for initial form rendering, submitted-data validation, error form rendering, and entry creation
- [ ] T041 [US3] Update `custom_components/rental_control/config_flow.py` so `_start_config_flow` remains importable with the current six-parameter signature and delegates to `custom_components/rental_control/config_flow_helpers/steps.py`
- [ ] T042 [US3] Ensure step helpers call the shell object's `_get_unique_id()` only when present, preserving `RentalControlFlowHandler._get_unique_id()` ownership and the `config_flow.gen_uuid` patch seam in `custom_components/rental_control/config_flow.py`
- [ ] T043 [US1] Ensure `custom_components/rental_control/config_flow_helpers/steps.py` does not add flow steps, change `user` or `init` step IDs, change `async_create_entry()` titles, write config entries outside `async_create_entry()`, perform extra URL fetches, or schedule async tasks
- [ ] T044 [US1] Run step-helper validation with `uv run pytest tests/unit/test_config_flow.py tests/unit/test_config_flow_validation.py -q` against the listed test files

**Checkpoint**: Step helpers prove FR-001 through FR-004, FR-007, FR-008,
FR-010, FR-014, SC-001, SC-003, SC-004, SC-005, and SC-008.

---

## Phase 6: Shell Delegation and `_show_config_form` Reduction (Priority: P1)

**Goal**: Slim `config_flow.py` into the Home Assistant shell, keep compatibility
wrappers, split the long functions, and reduce `_show_config_form` to no more
than six parameters with `ConfigFormContext`.

**Independent Test**: Import the shell, run config and options flows through Home
Assistant managers, call compatibility wrappers directly where practical, and
verify both internal form-render call sites use grouped context without changing
returned forms.

### Tests for Shell Delegation and Form Context

- [ ] T045 [US3] Add config-flow shell tests proving `RentalControlFlowHandler`, `RentalControlOptionsFlow`, `VERSION = 10`, `async_step_user`, `async_step_init`, and `async_get_options_flow` remain present on `custom_components.rental_control.config_flow` in `tests/unit/test_config_flow.py`
- [ ] T046 [US3] Add compatibility wrapper tests proving `_normalize_lock_entry`, `_get_schema`, `_show_config_form`, and `_start_config_flow` remain importable from `custom_components.rental_control.config_flow` and behave compatibly in `tests/unit/test_config_flow.py`
- [ ] T047 [US1] Add `ConfigFormContext` form-render tests proving `_show_config_form` returns the same schema, errors, placeholders, step ID, defaults, and options-only fields for config and options contexts in `tests/unit/test_config_flow_schemas.py`
- [ ] T048 [US1] Add internal call-site coverage proving the `_start_config_flow` error path and initial-form path both render through grouped context without changing `async_show_form()` output in `tests/unit/test_config_flow_validation.py`

### Implementation for Shell Delegation and Form Context

- [ ] T049 [US3] Update `custom_components/rental_control/config_flow.py` imports to use `config_flow_helpers` modules while keeping `RentalControlFlowHandler`, `RentalControlOptionsFlow`, `VERSION = 10`, `async_step_user`, `async_step_init`, `async_get_options_flow`, and module-level `gen_uuid` in the shell
- [ ] T050 [US3] Keep `RentalControlFlowHandler._get_unique_id()` in `custom_components/rental_control/config_flow.py` and ensure it calls module-level `gen_uuid(self.created)` at runtime rather than a helper-cached alias
- [ ] T051 [US4] Reduce `custom_components/rental_control/config_flow.py` `_get_schema` below 80 lines by delegating to focused schema builders without changing its current call semantics
- [ ] T052 [US4] Reduce `custom_components/rental_control/config_flow.py` `_start_config_flow` below 80 lines by delegating to focused step helpers without changing its current six-parameter compatibility signature
- [ ] T053 [US4] Change `custom_components/rental_control/config_flow.py` `_show_config_form` to accept a `ConfigFormContext` or compatibility-normalized context while declaring no more than six parameters and returning the same `async_show_form()` result
- [ ] T054 [US4] Update both internal `_show_config_form` call sites in the `_start_config_flow` error re-render path and initial-form path to pass `ConfigFormContext` in `custom_components/rental_control/config_flow.py` or `custom_components/rental_control/config_flow_helpers/steps.py`
- [ ] T055 [US4] Remove temporary extraction shims from `custom_components/rental_control/config_flow.py` and `custom_components/rental_control/config_flow_helpers/*.py`, leaving only planned wrappers, helper exports, typed context values, and focused helper functions
- [ ] T056 [US1] Run shell and form-context validation with `uv run pytest tests/unit/test_config_flow.py tests/unit/test_config_flow_schemas.py tests/unit/test_config_flow_validation.py -q` against the listed test files

**Checkpoint**: Shell delegation and form-context reduction prove FR-001 through
FR-010, FR-014, SC-001 through SC-005, and SC-008 with the public flow still
hosted by `config_flow.py`.

---

## Phase 7: Import and Patch-Seam Verification (Priority: P1)

**Goal**: Preserve Home Assistant discovery, visible and hidden compatibility
surfaces, and the `gen_uuid` monkeypatch seam after helper delegation.

**Independent Test**: Import the config-flow module, patch
`custom_components.rental_control.config_flow.gen_uuid`, drive duplicate
detection through the Home Assistant flow manager, and verify runtime calls
observe the patched object without requiring callers to import helper modules.

### Tests for Compatibility Seams

- [ ] T057 [US3] Add import-surface tests proving `RentalControlFlowHandler`, `RentalControlOptionsFlow`, `gen_uuid`, `_normalize_lock_entry`, `_get_schema`, `_show_config_form`, and `_start_config_flow` remain importable from `custom_components.rental_control.config_flow` in `tests/unit/test_config_flow.py`
- [ ] T058 [US3] Add patch-seam tests proving patches to `custom_components.rental_control.config_flow.gen_uuid` still affect duplicate detection through `RentalControlFlowHandler._get_unique_id()` in `tests/unit/test_config_flow.py`
- [ ] T059 [US3] Add Home Assistant flow-manager smoke tests proving initial setup and options editing still initialize through the same domain, step IDs, flow classes, callbacks, and version metadata in `tests/unit/test_config_flow.py`
- [ ] T060 [US3] Add direct helper compatibility tests for hidden-consumer seams around `_get_schema`, `_show_config_form`, and `_start_config_flow` where practical without requiring production callers to import helper modules in `tests/unit/test_config_flow.py`

### Implementation Verification

- [ ] T061 [US3] Verify no production caller in `custom_components/rental_control/**/*.py` imports config-flow shell classes or compatibility helpers from `custom_components/rental_control/config_flow_helpers/` instead of `custom_components/rental_control/config_flow.py`
- [ ] T062 [US3] Verify helper imports in `custom_components/rental_control/config_flow_helpers/*.py` do not cache or bypass the config-flow module `gen_uuid` patch seam
- [ ] T063 [US3] Run import and patch-seam validation with `uv run pytest tests/unit/test_config_flow.py tests/integration/test_full_setup.py tests/integration/test_refresh_cycle.py -q` against the listed files

**Checkpoint**: Compatibility verification proves FR-003, FR-004, FR-010,
SC-001, SC-004, and SC-005 before maintainability cleanup.

---

## Phase 8: Maintainability, File Sizes, and Aislop Gates (Priority: P2)

**Goal**: Resolve active config-flow complexity findings without suppressions,
catch-all modules, or behavior changes.

**Independent Test**: Measure every config-flow-related file after final shell
wiring and run existing complexity tooling with the hallucinated-import directive
still present and no new complexity directive added.

### Cleanup and Complexity Gates

- [ ] T064 [US4] Confirm final implementation diff is limited to `custom_components/rental_control/config_flow.py`, `custom_components/rental_control/config_flow_helpers/*.py`, and directly required config-flow tests under `tests/unit/` or `tests/integration/`
- [ ] T065 [US4] Confirm no new configuration options, flow steps, validation rules, error keys, defaults, selector choices, entry data keys, options data keys, public caller behavior, Home Assistant state writes, config-entry writes outside current flow transitions, calendar fetches, Keymaster service calls, blocking I/O, async tasks, or user-visible delays were introduced in config-flow files
- [ ] T066 [US4] Measure `custom_components/rental_control/config_flow.py` and `custom_components/rental_control/config_flow_helpers/*.py` with `wc -l` and confirm every config-flow-related file is below 400 lines
- [ ] T067 [US4] Ensure every project-owned function in `custom_components/rental_control/config_flow.py` and `custom_components/rental_control/config_flow_helpers/*.py` is below 80 lines, including `_get_schema` and `_start_config_flow`
- [ ] T068 [US4] Ensure every project-owned parameter list in `custom_components/rental_control/config_flow.py` and `custom_components/rental_control/config_flow_helpers/*.py` has no more than six parameters unless an external Home Assistant framework signature requires otherwise, including `_show_config_form`
- [ ] T069 [US4] Verify the existing `# aislop-ignore-file ai-slop/hallucinated-import -- Provided by Home Assistant runtime.` directive remains present in `custom_components/rental_control/config_flow.py`
- [ ] T070 [US4] Verify no new `aislop-ignore`, `aislop-ignore-file`, or equivalent complexity suppression was added to `custom_components/rental_control/config_flow.py` or `custom_components/rental_control/config_flow_helpers/*.py`
- [ ] T071 [US4] Run isolated complexity validation with `uv run pre-commit run aislop` and confirm file-size, function-length, and parameter-count thresholds pass for the config-flow decomposition

**Checkpoint**: Maintainability proves FR-011, FR-012, FR-013, FR-014,
SC-006, SC-007, and SC-008.

---

## Phase 9: Polish & Cross-Cutting Acceptance Gates

**Purpose**: Verify behavior parity, caller compatibility, quality gates,
traceability, and implementation notes before the runtime refactor is complete.

### Acceptance and Quality Gates

- [ ] T072 Run unchanged config-flow parity tests with `uv run pytest tests/unit/test_config_flow.py -x -q` against `tests/unit/test_config_flow.py`
- [ ] T073 Run all new focused helper tests with `uv run pytest tests/unit/test_config_flow_schemas.py tests/unit/test_config_flow_validation.py -q` against the listed test files
- [ ] T074 Run integration caller parity tests with `uv run pytest tests/integration/test_full_setup.py tests/integration/test_refresh_cycle.py -x -q` against the listed integration files
- [ ] T075 Run full regression tests with `uv run pytest tests/ -x -q` against `tests/`
- [ ] T076 Run linting with `uv run ruff check custom_components/ tests/` against `custom_components/` and `tests/`
- [ ] T077 Run full pre-commit validation with `uv run pre-commit run --all-files` against repository-tracked files, including reuse, yamllint, actionlint, aislop, ruff, ruff-format, mypy, interrogate, and gitlint hooks
- [ ] T078 Verify every FR-001 through FR-014 has a test, implementation, or acceptance task mapped in `specs/020-decompose-config-flow/tasks.md`
- [ ] T079 Verify every SC-001 through SC-009 has a test, implementation, or acceptance task mapped in `specs/020-decompose-config-flow/tasks.md`
- [ ] T080 Review `specs/020-decompose-config-flow/quickstart.md` and confirm the implementation PR notes list unchanged parity commands, focused schema and validation commands, import and patch-seam results, `_show_config_form` parameter-count results, file-size measurements, final `aislop` results, full `pytest tests/ -x -q`, ruff, and pre-commit results

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup & Baseline (Phase 1)**: No dependencies.
- **Helper Models and `ConfigFormContext` (Phase 2)**: Depends on Setup because
  model shapes must reflect the live source and tests.
- **Schema Builder Extraction (Phase 3)**: Depends on models where grouped schema
  context or defaults normalization is used.
- **Validation and Conversion Helpers (Phase 4)**: Depends on baseline and can
  proceed after model shapes are available; successful conversion depends on
  shared lock and generator conversion behavior.
- **Step Transition Helpers (Phase 5)**: Depends on schema and validation helpers
  so orchestration can delegate without changing behavior.
- **Shell Delegation and `_show_config_form` Reduction (Phase 6)**: Depends on
  helpers being present so shell functions can become wrappers and grouped form
  context can replace expanded parameters at both internal call sites.
- **Import and Patch-Seam Verification (Phase 7)**: Depends on shell delegation
  and call-time `gen_uuid` ownership.
- **Maintainability (Phase 8)**: Depends on all extraction, wrapper wiring,
  compatibility verification, and shim cleanup.
- **Polish (Phase 9)**: Depends on all desired extraction, wrapper,
  compatibility, and cleanup phases.

### User Story Dependencies

- **US1 (P1)**: Initial config flow parity starts with baseline tests and drives
  schema, validation, step transitions, shell delegation, and final parity gates.
- **US2 (P1)**: Options flow parity starts with baseline tests and is proven by
  options schema tests, lock/default conversion, options step transitions, and
  final integration gates.
- **US3 (P1)**: Compatibility surface depends on shell delegation and completes
  with import, private seam, Home Assistant flow-manager, and `gen_uuid` patch
  tests.
- **US4 (P2)**: Maintainability follows helper extraction and shell wiring because
  file/function/parameter thresholds are meaningful only after the split.

### Within Each Story

- Focused tests are written before the corresponding helper extraction tasks and
  should fail or expose missing coverage until the extraction lands.
- `models.py` precedes helpers that need grouped form, validation, or transition
  context values.
- `schemas.py` and `validation.py` precede `steps.py` orchestration.
- `config_flow.py` keeps Home Assistant classes, `VERSION = 10`, step methods,
  callback registration, `gen_uuid`, and compatibility wrappers throughout
  extraction.
- `_show_config_form` call-site changes happen with `ConfigFormContext`, not
  before the context exists.
- File-size measurement and `uv run pre-commit run aislop` happen after temporary
  shims are removed and before final full gates.
- The hallucinated-import directive must remain; no complexity directive may be
  added at any point.

---

## Parallel Opportunities

- T016 and T017 can be developed in parallel after baselines because initial and
  options schema parity can be split across independent test sections or agents.
- T024 and T025 can be developed in parallel after models because URL validation
  and scalar validation tests cover different helper concerns.
- T019, T029, and T040 create different helper modules and can be developed in
  parallel once model shapes are stable, with integration sequenced afterward.
- Shell tests in `tests/unit/test_config_flow.py` should be sequenced within one
  working copy because import, wrapper, flow-manager, and patch-seam assertions
  edit the same file.
- T072, T073, and T074 can run independently once implementation is complete;
  T075 through T077 are final serial quality gates.

## Parallel Example: Helper Parity After Models

```bash
Task: "Add initial config-flow schema parity tests in tests/unit/test_config_flow_schemas.py"
Task: "Add URL validation parity tests in tests/unit/test_config_flow_validation.py"
Task: "Create schema builders in custom_components/rental_control/config_flow_helpers/schemas.py"
```

---

## Implementation Strategy

### MVP First (Behavior Parity and Safety)

1. Complete Phase 1 baselines.
2. Add helper models and focused tests for exact current values.
3. Extract schema builders while keeping `_get_schema` as a wrapper.
4. Extract validation and conversion helpers while preserving mutation timing.
5. Add step helpers and slim `_start_config_flow` into a compatibility wrapper.
6. Reduce `_show_config_form` with `ConfigFormContext` and update both internal
   call sites.
7. Verify import and `gen_uuid` patch seams before claiming maintainability fixes.

### Incremental Delivery

1. Build `config_flow_helpers/models.py` and keep it internal.
2. Build `schemas.py`, `validation.py`, and `steps.py` with focused parity tests.
3. Slim `config_flow.py` into the Home Assistant compatibility shell with wrapper
   functions and call-time patch dependencies.
4. Remove temporary shims, measure every config-flow file below 400 lines, run
   `aislop` with no new suppression, and then run full pytest, ruff, and
   pre-commit gates.

---

## Acceptance Coverage Map

| Coverage item | Primary tasks |
|---------------|---------------|
| US1 Preserve initial config flow | T005-T009, T016, T018-T023, T024-T026, T028-T035, T036-T038, T040-T044, T047-T056, T063, T072-T077 |
| US2 Preserve options flow behavior | T005-T009, T017, T022-T023, T027, T034-T035, T039-T044, T047-T056, T063, T072-T077 |
| US3 Preserve config-flow compatibility surface | T006, T030, T041-T042, T045-T046, T049-T050, T057-T063, T072-T079 |
| US4 Improve maintainability under Aislop limits | T009-T015, T019, T029, T040, T051-T055, T064-T071, T076-T080 |
| FR-001 observable behavior unchanged | T007-T008, T016-T018, T023-T028, T035-T039, T044, T056, T063, T072-T075 |
| FR-002 existing tests and parity tests | T007-T008, T016-T018, T023-T028, T035-T039, T044, T056, T063, T072-T075 |
| FR-003 HA public surface retained | T005-T006, T041-T042, T045-T046, T049-T050, T057-T063, T072 |
| FR-004 compatibility surface retained | T006, T018, T030, T041, T046, T057-T063, T072 |
| FR-005 schema behavior equivalent | T016-T023, T047, T056, T072-T073 |
| FR-006 schema builders focused | T019-T023, T051, T066-T071 |
| FR-007 start-flow behavior equivalent | T024-T044, T052, T056, T072-T073 |
| FR-008 validation and transition split | T029-T044, T052, T066-T071 |
| FR-009 form context parameter reduction | T010-T015, T047-T048, T053-T054, T068 |
| FR-010 step IDs and HA registration retained | T036-T046, T049-T050, T057-T063, T072-T074 |
| FR-011 complexity thresholds | T009, T014, T051-T055, T064-T071, T077 |
| FR-012 hallucinated-import directive retained | T009, T069, T071, T077 |
| FR-013 no complexity suppression | T009, T070-T071, T077 |
| FR-014 behavior-preserving docs and scope | T002-T004, T065, T078-T080 |
| SC-001 existing tests green | T007-T008, T023, T035, T044, T056, T063, T072-T075 |
| SC-002 schema render parity | T016-T023, T047, T056, T072-T073 |
| SC-003 validation parity | T024-T035, T037-T039, T044, T072-T073 |
| SC-004 HA flow initialization unchanged | T045-T046, T049-T050, T057-T063, T072-T074 |
| SC-005 visible import and patch seams unchanged | T006, T030, T041-T042, T046, T057-T063, T072 |
| SC-006 file/function/parameter limits | T009, T014, T051-T055, T064-T068, T071, T077 |
| SC-007 no complexity directive | T009, T069-T071, T077 |
| SC-008 no added hot-path work | T031, T043, T065, T072-T077 |
| SC-009 docs-only tasks stage | This `tasks.md` PR only; implementation tasks start unchecked |
