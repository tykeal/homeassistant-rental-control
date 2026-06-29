<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Tasks: Decompose Util

**Input**: Design documents from `/specs/018-decompose-util/`
**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅,
quickstart.md ✅

**Tests**: Included and required. This feature is a behavior-preserving
refactor of the 1,173-line `custom_components/rental_control/util.py`, so
existing utility, event override, coordinator, refresh-cycle, slot-concurrency,
sensor, calendar, and helper tests remain the primary oracle. New focused tests
pin helper parity, Keymaster service-call ordering, state-change callback
semantics, util import compatibility, and every monkeypatch path that must keep
intercepting runtime calls.

**Organization**: Tasks are grouped by setup, the ordered split from PLAN
(generic helpers first, Keymaster service helpers second, state-change handlers
third), shell re-export wrappers, caller and patch-target verification,
maintainability under active `aislop` thresholds, and final gates. Implementation
must keep `custom_components/rental_control/util.py` as the public compatibility
shell while moving implementation detail into sibling modules.

## Format: `- [x] T### [P?] [Story?] Description with file path`

- **[P]**: Can run in parallel (different files, no dependency on incomplete
  tasks)
- **[Story]**: Which user story the task primarily proves (US1 through US5)
- Include exact file paths in descriptions
- Leave every checkbox unchecked until the implementation PR performs the task

## Path Conventions

- **Public shell**: `custom_components/rental_control/util.py`
- **Extracted sibling modules**:
  `custom_components/rental_control/helpers.py`,
  `custom_components/rental_control/keymaster_services.py`, and
  `custom_components/rental_control/state_handlers.py`
- **Caller modules with compatibility patch boundaries**:
  `custom_components/rental_control/event_overrides.py` and
  `custom_components/rental_control/coordinator.py`
- **Production callers that must keep util imports source-compatible**:
  `custom_components/rental_control/__init__.py`,
  `custom_components/rental_control/calendar.py`,
  `custom_components/rental_control/config_flow.py`,
  `custom_components/rental_control/listeners.py`,
  `custom_components/rental_control/switch.py`,
  `custom_components/rental_control/event_overrides_helpers/*.py`,
  `custom_components/rental_control/coordinator_helpers/*.py`, and
  `custom_components/rental_control/sensors/**/*.py`
- **Required util compatibility surface**: `dt`, `add_call`, `apply_buffer`,
  `async_fire_clear_code`, `async_fire_set_code`, `async_fire_update_times`,
  `async_reload_package_platforms`, `check_gather_results`,
  `compute_early_expiry_time`, `delete_folder`, `delete_rc_and_base_folder`,
  `EventIdentity`, `gen_uuid`, `get_entry_data`, `get_event_identities`,
  `get_event_names`, `get_slot_name`, `handle_state_change`,
  `is_cleared_keymaster_text_state`, `is_unreadable_keymaster_text_state`,
  `normalize_keymaster_text_state`, `normalize_uid`, `OperationResult`, and
  `trim_name`
- **Patch-sensitive util attributes that must remain on `util.py`**: `asyncio`,
  `async_track_state_change_event`, `pn_create`, `pn_dismiss`, and
  `_SET_CODE_CONFIRMATION_TIMEOUT`
- **Existing behavior-oracle tests**: `tests/unit/test_util.py`,
  `tests/unit/test_event_overrides.py`, `tests/unit/test_event_overrides_apply.py`,
  `tests/unit/test_coordinator.py`, `tests/unit/test_coordinator_buffer_update.py`,
  `tests/unit/test_sensors.py`, `tests/integration/test_refresh_cycle.py`, and
  `tests/integration/test_slot_concurrency.py`
- **New focused tests**: `tests/unit/test_keymaster_services.py`,
  `tests/unit/test_state_handlers.py`, and optional focused additions to
  `tests/unit/test_util.py`, `tests/unit/test_event_overrides.py`, and
  `tests/unit/test_coordinator.py`
- **Feature docs**: `specs/018-decompose-util/`

## Live Module Transition Scope

Implementation changes the live utility feature only. The target split from PLAN
is:

- `custom_components/rental_control/util.py` — public compatibility shell, full
  import surface, patch-sensitive dependency attributes, thin wrappers for
  `async_fire_*`, `get_event_identities`, `get_event_names`, and
  `handle_state_change`, plus any compatibility-only names that tests patch.
- `custom_components/rental_control/helpers.py` — generic stable behavior:
  text-state helpers, `OperationResult`, `EventIdentity`, entry-data lookup,
  UID normalization, gather checking, service-call collection, recursive cleanup,
  reload helper, UUID generation, `trim_name`, `apply_buffer`, datetime coercion,
  early-expiry calculation, slot-name parsing, and event identity builders.
- `custom_components/rental_control/keymaster_services.py` — implementation of
  `async_fire_set_code`, `async_fire_clear_code`, and
  `async_fire_update_times`, plus operation request/dependency objects,
  service-call builders, confirmation helpers, and result finalizers.
- `custom_components/rental_control/state_handlers.py` — implementation behind
  `handle_state_change`, including context resolution, state snapshots,
  text-state normalization, datetime parsing, trim/prefix restoration, and
  final override dispatch.
- `custom_components/rental_control/event_overrides.py` — keeps visible
  module-level wrappers named `async_fire_set_code`, `async_fire_clear_code`,
  `async_fire_update_times`, and `get_event_identities`; each wrapper calls the
  `util` module at runtime so `EventOverrides._module` patches and util-level
  patches both remain effective.
- `custom_components/rental_control/coordinator.py` — keeps visible
  module-level `async_fire_clear_code` and `add_call` compatibility names;
  `async_fire_clear_code` calls `util.async_fire_clear_code` at runtime so both
  coordinator-level and util-level patches remain effective.

No implementation task may add an `aislop-ignore-file` directive to `util.py` or
to any extracted utility module. The active file-size and function-length
findings must be resolved by decomposition.

---

## Phase 1: Setup & Baseline (Shared Infrastructure)

**Purpose**: Establish behavior, import, patch-site, caller, and complexity
baselines before moving any production code.

- [x] T001 Run `.specify/scripts/bash/check-prerequisites.sh --json` with `SPECIFY_FEATURE=018-decompose-util` from the repository root and confirm `specs/018-decompose-util/` reports `research.md`, `data-model.md`, and `quickstart.md`
- [x] T002 Inspect US1-US5, FR-001 through FR-019, edge cases, assumptions, non-goals, security considerations, and SC-001 through SC-010 in `specs/018-decompose-util/spec.md`
- [x] T003 Inspect the Project Structure, Concrete Decomposition Design, compatibility boundary, monkeypatch preservation, helper split, complexity, and phase notes in `specs/018-decompose-util/plan.md`
- [x] T004 Inspect all research decisions, all data-model request/dependency objects, and quickstart import, patch, test, and complexity checks in `specs/018-decompose-util/research.md`, `specs/018-decompose-util/data-model.md`, and `specs/018-decompose-util/quickstart.md`
- [x] T005 Inventory `is_cleared_keymaster_text_state`, `is_unreadable_keymaster_text_state`, `normalize_keymaster_text_state`, `OperationResult`, `get_entry_data`, `normalize_uid`, `check_gather_results`, `add_call`, `delete_rc_and_base_folder`, `delete_folder`, `trim_name`, `apply_buffer`, `_ensure_datetime`, `EventIdentity`, `get_event_identities`, `get_event_names`, `gen_uuid`, `compute_early_expiry_time`, `get_slot_name`, and `async_reload_package_platforms` in `custom_components/rental_control/util.py`
- [x] T006 Inventory `async_fire_set_code`, `async_fire_clear_code`, `async_fire_update_times`, `_async_wait_for_expected_name`, `_async_wait_for_expected_datetime`, service-call ordering, retry notification handling, ownership guards, and confirmation behavior in `custom_components/rental_control/util.py`
- [x] T007 Inventory `handle_state_change` reset, suppression, state-read, text-normalization, datetime-parsing, trim/prefix restoration, and `update_event_overrides` dispatch behavior in `custom_components/rental_control/util.py`
- [x] T008 Inventory every production util import in `custom_components/rental_control/__init__.py`, `calendar.py`, `config_flow.py`, `listeners.py`, `switch.py`, `event_overrides.py`, `coordinator.py`, `event_overrides_helpers/*.py`, `coordinator_helpers/*.py`, and `sensors/**/*.py`; record that caller imports must remain source-compatible unless a wrapper import is explicitly needed for runtime patch delegation
- [x] T009 Inventory visible patch targets in `tests/unit/test_util.py`, `tests/unit/test_event_overrides.py`, `tests/unit/test_event_overrides_apply.py`, `tests/unit/test_coordinator.py`, `tests/unit/test_coordinator_buffer_update.py`, `tests/integration/test_refresh_cycle.py`, and `tests/integration/test_slot_concurrency.py`, including `util.*`, `event_overrides.*`, and `coordinator.*` monkeypatch paths
- [x] T010 Run unchanged baseline utility parity tests with `uv run pytest tests/unit/test_util.py tests/unit/test_event_overrides.py tests/unit/test_event_overrides_apply.py tests/unit/test_coordinator.py tests/unit/test_coordinator_buffer_update.py tests/integration/test_refresh_cycle.py tests/integration/test_slot_concurrency.py -x -q` against the listed test files
- [x] T011 Record the current line, function-length, and parameter-count baseline for `custom_components/rental_control/util.py`, confirming the 1,173-line file, `handle_state_change` at 204 lines, `async_fire_set_code` at 174 lines, `async_fire_clear_code` at 122 lines, `async_fire_update_times` at 86 lines, no parameter list over six parameters, and no existing utility `aislop-ignore-file` directive

---

## Phase 2: Generic Helpers Extraction (Priority: P1)

**Goal**: Preserve generic helper semantics while moving them into
`helpers.py` before Keymaster service and state-handler extraction.

**Independent Test**: Compare helper outputs and side effects for identical text
states, buffers, calendar events, UIDs, names, cleanup paths, gather results,
service-call collection, package reloads, UUID inputs, and early-expiry inputs.

### Tests for Generic Helpers

- [x] T012 [US4] Add focused text-state tests for `is_cleared_keymaster_text_state`, `is_unreadable_keymaster_text_state`, and `normalize_keymaster_text_state` covering `None`, empty strings, whitespace, `unknown`, `none`, `unavailable`, mixed case, and populated values in `tests/unit/test_util.py`
- [x] T013 [US4] Add `apply_buffer` and `_ensure_datetime` parity tests for zero buffers, positive before/after buffers, bare `date`, naive `datetime`, timezone-aware `datetime`, string dates, invalid values, and non-integer optional buffers in `tests/unit/test_util.py`
- [x] T014 [US4] Add event identity and naming parity tests for Airbnb, VRBO, Tripadvisor, Booking.com, Guesty API, Guesty, blocked/unavailable, prefix stripping, normalized UIDs, date normalization, and degenerate fallback in `tests/unit/test_util.py`
- [x] T015 [US4] Add generic helper parity tests for `check_gather_results`, `add_call`, `delete_folder`, `delete_rc_and_base_folder`, `get_entry_data`, `gen_uuid`, `compute_early_expiry_time`, and `async_reload_package_platforms` in `tests/unit/test_util.py`

### Implementation for Generic Helpers

- [x] T016 [US4] Create `custom_components/rental_control/helpers.py` with SPDX headers, a module docstring, typed helper exports, and no imports from `custom_components/rental_control/util.py`
- [x] T017 [US4] Move text-state token, cleared, unreadable, and normalization helpers from `custom_components/rental_control/util.py` into `custom_components/rental_control/helpers.py` with byte-for-byte equivalent observed behavior
- [x] T018 [US4] Move `OperationResult`, `EventIdentity`, `get_entry_data`, `normalize_uid`, `check_gather_results`, `add_call`, `delete_folder`, `delete_rc_and_base_folder`, `gen_uuid`, `compute_early_expiry_time`, and `async_reload_package_platforms` behavior into `custom_components/rental_control/helpers.py`
- [x] T019 [US4] Move `trim_name`, `_ensure_datetime`, `apply_buffer`, `get_slot_name`, and non-wrapper event identity builder behavior into `custom_components/rental_control/helpers.py` while preserving all current return values, exceptions, and logging decisions relied on by tests
- [x] T020 [US4] Keep Home Assistant service-call collection, package reload, recursive cleanup, and entry-data lookup side effects identical when called through helpers in `custom_components/rental_control/helpers.py`
- [x] T021 [US4] Verify `custom_components/rental_control/helpers.py` stays below 400 lines, every project-owned function stays below 80 lines, and no helper-owned parameter list exceeds six parameters; split by stable sub-concern instead of adding a directive if it approaches the limit
- [x] T022 [US4] Run generic helper validation with `uv run pytest tests/unit/test_util.py tests/unit/test_sensors.py -q` against the listed test files

**Checkpoint**: Generic helper extraction proves FR-011, FR-012, FR-013,
FR-014, the helper portions of FR-001 through FR-003, and SC-004 before
Keymaster service helpers depend on the new module.

---

## Phase 3: Keymaster Service Helpers Extraction (Priority: P1)

**Goal**: Preserve physical lock-code service safety while splitting set, clear,
and update-times behavior into focused helpers below 80 lines.

**Independent Test**: Apply identical coordinator, event, slot, state, retry,
notification, and service-call fixtures and compare ordered Home Assistant
service calls, operation results, confirmation waits, lingering flags, retry
state, notification creation/dismissal, and cancellation propagation.

### Tests for Keymaster Services

- [x] T023 [US1] Add `KeymasterServiceDeps` construction tests proving util-supplied `asyncio.sleep`, `async_track_state_change_event`, `_SET_CODE_CONFIRMATION_TIMEOUT`, `pn_create`, `pn_dismiss`, and logger dependencies are read at wrapper call time in `tests/unit/test_keymaster_services.py`
- [x] T024 [US1] Add set-code parity tests for lock-name guard, prefix and `trim_name` calculation, ownership verification, buffer application, date coercion failures, exact service-call order, gather-result propagation, retry escalation, notification creation, confirmation wait, retry-success dismissal, and `OperationResult` fields in `tests/unit/test_keymaster_services.py`
- [x] T025 [US1] Add clear-code parity tests for lock-name guard, expected-name ownership verification, reset button call, cancellation propagation, retry escalation, propagation sleep, unreadable or missing name/PIN states, forced name clear, lingering-name and lingering-PIN classification, notification dismissal, and `OperationResult` fields in `tests/unit/test_keymaster_services.py`
- [x] T026 [US1] Add update-times parity tests for slot and lock-name guards, ownership verification, buffer application, invalid dates, end-before-start service-call ordering, gather-result handling, start/end confirmation waits, unconfirmed results, and failed results in `tests/unit/test_keymaster_services.py`
- [x] T027 [US1] Add regression tests confirming raw slot PIN values are not stored in `OperationResult` or retry/diagnostic helper values produced by `custom_components/rental_control/keymaster_services.py` in `tests/unit/test_keymaster_services.py`

### Implementation for Keymaster Services

- [x] T028 [US1] Create `custom_components/rental_control/keymaster_services.py` with SPDX headers, a module docstring, `KeymasterServiceDeps`, request/result helper types as needed, and no import-time binding to patch-sensitive `util` attributes
- [x] T029 [US1] Implement `async_fire_set_code` in `custom_components/rental_control/keymaster_services.py` using short helpers for slot display name, buffered window, slot disable, date-range enable, end/start/PIN/name writes, slot enable, operation failure, and expected-name confirmation
- [x] T030 [US1] Implement `async_fire_clear_code` in `custom_components/rental_control/keymaster_services.py` using short helpers for clear verification, reset button press, clear snapshot reads after util-supplied sleep, name classification, forced name clear, PIN classification, retry bookkeeping, and result finalization
- [x] T031 [US1] Implement `async_fire_update_times` in `custom_components/rental_control/keymaster_services.py` using short helpers for request verification, buffered update window, end-before-start writes, gather-result classification, and start/end datetime confirmation
- [x] T032 [US1] Preserve cancellation propagation and ordinary exception classification in `custom_components/rental_control/keymaster_services.py` for set, clear, and update-times operations exactly as current `util.py` behavior
- [x] T033 [US1] Preserve retry failure escalation, persistent-notification creation, retry-success recording, and notification dismissal IDs in `custom_components/rental_control/keymaster_services.py` exactly as current `util.py` behavior
- [x] T034 [US1] Verify `custom_components/rental_control/keymaster_services.py` performs no extra Home Assistant state writes, extra Keymaster service calls, Store authority, coordinator refreshes, blocking I/O, or user-visible delays beyond current `util.py` behavior
- [x] T035 [US1] Ensure every project-owned function in `custom_components/rental_control/keymaster_services.py` is below 80 lines and every project-owned parameter list is no more than six parameters by using request and dependency objects where needed
- [x] T036 [US1] Run Keymaster service validation with `uv run pytest tests/unit/test_keymaster_services.py tests/unit/test_util.py tests/unit/test_event_overrides_apply.py tests/unit/test_event_overrides.py tests/integration/test_slot_concurrency.py -q` against the listed test files

**Checkpoint**: Keymaster service extraction proves FR-004, FR-007, FR-008,
FR-009, FR-018, SC-002, and the physical-access safety portions of SC-001.

---

## Phase 4: State-Change Handler Extraction (Priority: P1)

**Goal**: Preserve `handle_state_change` callback semantics while moving staged
read, normalize, parse, restore, and update decisions into `state_handlers.py`.

**Independent Test**: Replay identical reset, suppression, enabled-state,
code/name/date, unreadable-state, empty-state, date-range, trim/prefix, and
feedback events and compare early returns plus final override update arguments.

### Tests for State Handlers

- [x] T037 [US2] Add `StateHandlerDeps` construction tests proving util-supplied `asyncio.sleep` is read at `handle_state_change` wrapper call time and continues to let current tests skip the settle delay in `tests/unit/test_state_handlers.py`
- [x] T038 [US2] Add state-change context tests for missing lock name, missing event overrides, malformed entity ids, slot-number extraction, debug/warning behavior, and current early returns in `tests/unit/test_state_handlers.py`
- [x] T039 [US2] Add reset and suppression tests proving reset entities call `event_overrides.async_update(slot, "", "", start_of_local_day, start_of_local_day)` and coordinator feedback events short-circuit without override updates in `tests/unit/test_state_handlers.py`
- [x] T040 [US2] Add state snapshot tests for enabled-slot gates, missing code or name states, unreadable values, code-without-name protection, date-range switch handling, and no added service calls or refresh requests in `tests/unit/test_state_handlers.py`
- [x] T041 [US2] Add datetime and feedback preservation tests for event-supplied start/end values, existing override preservation during feedback, unparsable datetimes, local-day defaults, and date-range-off behavior in `tests/unit/test_state_handlers.py`
- [x] T042 [US2] Add trim/prefix restoration tests proving full-name restoration occurs only when the incoming Keymaster name matches the expected `trim_name` result and manual external name edits remain honored in `tests/unit/test_state_handlers.py`
- [x] T043 [US2] Add final dispatch tests proving `coordinator.update_event_overrides` receives the same five positional values and state-change callbacks do not launch reconciliation in `tests/unit/test_state_handlers.py`

### Implementation for State Handlers

- [x] T044 [US2] Create `custom_components/rental_control/state_handlers.py` with SPDX headers, a module docstring, `StateHandlerDeps`, staged helper types as needed, and no import-time binding to patch-sensitive `util` attributes
- [x] T045 [US2] Implement context resolution, slot-number extraction, reset handling, and suppression checks in `custom_components/rental_control/state_handlers.py` with the same warnings, debug messages, and early returns as current `util.py`
- [x] T046 [US2] Implement slot state snapshot reads in `custom_components/rental_control/state_handlers.py` for enabled, code, name, date-range, start, and end entities without adding service calls, Store writes, refreshes, or reconciliation launches
- [x] T047 [US2] Implement text-state normalization and code-without-name protection in `custom_components/rental_control/state_handlers.py` using the extracted helpers while preserving existing-override values during feedback paths
- [x] T048 [US2] Implement datetime parsing, local-day defaults, existing override time preservation, trim/prefix full-name restoration, and final override dispatch in `custom_components/rental_control/state_handlers.py`
- [x] T049 [US2] Ensure every project-owned function in `custom_components/rental_control/state_handlers.py` is below 80 lines and every project-owned parameter list is no more than six parameters by using context, snapshot, and update payload objects where needed
- [x] T050 [US2] Run state-handler validation with `uv run pytest tests/unit/test_state_handlers.py tests/unit/test_util.py tests/unit/test_event_overrides.py tests/integration/test_refresh_cycle.py -q` against the listed test files

**Checkpoint**: State-handler extraction proves FR-010, FR-018, SC-003, and the
callback portions of SC-001 without changing passive callback semantics.

---

## Phase 5: Util Shell Re-Exports and Runtime Wrappers (Priority: P1)

**Goal**: Slim `util.py` into the compatibility shell while keeping the full
import surface and all util-level monkeypatch seams patchable through runtime
lookup, not bound aliases.

**Independent Test**: Import every required util symbol from
`custom_components.rental_control.util`, patch util-level service and event
identity wrappers, patch util-level dependency attributes, and verify callers
observe patched objects without caller import rewrites.

### Tests for Util Shell Compatibility

- [x] T051 [US3] Add util import-surface tests proving every name in the Path Conventions compatibility surface remains importable from `custom_components.rental_control.util` in `tests/unit/test_util.py`
- [x] T052 [US3] Add util compatibility-attribute tests proving `asyncio`, `async_track_state_change_event`, `pn_create`, `pn_dismiss`, and `_SET_CODE_CONFIRMATION_TIMEOUT` remain patchable on `custom_components.rental_control.util` in `tests/unit/test_util.py`
- [x] T053 [US3] Add util wrapper tests proving patches to `custom_components.rental_control.util.async_fire_set_code`, `async_fire_clear_code`, `async_fire_update_times`, and `get_event_identities` intercept runtime calls in `tests/unit/test_util.py`
- [x] T054 [US3] Add `get_event_names` patch-seam tests proving it calls module-level `util.get_event_identities()` at runtime rather than a copied helper alias in `tests/unit/test_util.py`
- [x] T055 [US3] Add util dependency wrapper tests proving patched `util.asyncio.sleep`, `util.async_track_state_change_event`, `util.pn_create`, `util.pn_dismiss`, and `util._SET_CODE_CONFIRMATION_TIMEOUT` are passed to extracted service and state implementations at wrapper call time in `tests/unit/test_util.py`

### Implementation for Util Shell Compatibility

- [x] T056 [US3] Replace generic helper bodies in `custom_components/rental_control/util.py` with shell exports from `custom_components/rental_control/helpers.py` while preserving all public names, docstring coverage, and compatibility attributes
- [x] T057 [US3] Implement thin `util.async_fire_set_code`, `util.async_fire_clear_code`, and `util.async_fire_update_times` wrappers in `custom_components/rental_control/util.py` that construct dependency objects from current util module attributes and delegate to `keymaster_services` at runtime
- [x] T058 [US3] Implement thin `util.handle_state_change` wrapper in `custom_components/rental_control/util.py` that constructs `StateHandlerDeps` from current util module attributes and delegates to `state_handlers` at runtime
- [x] T059 [US3] Implement thin `util.get_event_identities` and `util.get_event_names` wrappers in `custom_components/rental_control/util.py` so util-level event identity patches remain effective and `get_event_names` never calls a stale copied alias
- [x] T060 [US3] Verify `custom_components/rental_control/util.py` does not use direct bound aliases for patch-sensitive wrappers and keeps non-patch-sensitive re-exports source-compatible for production callers and tests
- [x] T061 [US3] Run util shell validation with `uv run pytest tests/unit/test_util.py tests/unit/test_keymaster_services.py tests/unit/test_state_handlers.py -q` against the listed test files

**Checkpoint**: The util shell proves FR-003, FR-004, FR-005, FR-015,
FR-016, SC-005, SC-006, and the import-surface portions of SC-001.

---

## Phase 6: Caller Imports and Patch-Target Verification (Priority: P1)

**Goal**: Preserve every visible and hidden monkeypatch boundary by updating only
intentional wrappers and proving production caller imports stay source-compatible.

**Independent Test**: Patch `util.*`, `event_overrides.*`, and `coordinator.*`
paths independently and verify the same call sites are intercepted as before,
including `EventOverrides._module` indirection.

### Tests for Caller and Patch Compatibility

- [x] T062 [US3] Add event-overrides patch tests proving `custom_components.rental_control.event_overrides.async_fire_set_code`, `async_fire_clear_code`, `async_fire_update_times`, and `get_event_identities` remain visible module-level patch targets used through `EventOverrides._module` in `tests/unit/test_event_overrides.py`
- [x] T063 [US3] Add event-overrides util-fallback tests proving unpatched `event_overrides.async_fire_*` and `event_overrides.get_event_identities` wrappers call `util.async_fire_*` and `util.get_event_identities` at runtime, so util-level patches intercept through the visible wrapper in `tests/unit/test_event_overrides_apply.py`
- [x] T064 [US3] Add coordinator patch tests proving `custom_components.rental_control.coordinator.async_fire_clear_code` and `custom_components.rental_control.coordinator.add_call` remain visible compatibility names for current tests in `tests/unit/test_coordinator.py`
- [x] T065 [US3] Add coordinator util-fallback tests proving unpatched `coordinator.async_fire_clear_code` calls `util.async_fire_clear_code` at runtime, so util-level patches intercept coordinator wrapper behavior in `tests/unit/test_coordinator.py`
- [x] T066 [US3] Add production import-boundary tests proving production modules keep importing required symbols from `custom_components.rental_control.util` and no production caller imports directly from `helpers.py`, `keymaster_services.py`, or `state_handlers.py` except the public util shell and intentional wrapper modules in `tests/unit/test_util.py`

### Implementation for Caller and Patch Compatibility

- [x] T067 [US3] Update `custom_components/rental_control/event_overrides.py` to keep module-level `async_fire_set_code`, `async_fire_clear_code`, `async_fire_update_times`, and `get_event_identities` wrappers that call `custom_components.rental_control.util` at runtime while preserving `EventOverrides._module = sys.modules[__name__]`
- [x] T068 [US3] Update `custom_components/rental_control/coordinator.py` to keep module-level `async_fire_clear_code` and `add_call` compatibility names, with `async_fire_clear_code` delegating to `util.async_fire_clear_code` at runtime and `add_call` preserving current patch behavior
- [x] T069 [US3] Verify no unintended caller import or call-style changes in `custom_components/rental_control/__init__.py`, `calendar.py`, `config_flow.py`, `listeners.py`, `switch.py`, `event_overrides_helpers/*.py`, `coordinator_helpers/*.py`, and `sensors/**/*.py`; revert any unplanned import diff
- [x] T070 [US3] Run caller and patch validation with `uv run pytest tests/unit/test_event_overrides.py tests/unit/test_event_overrides_apply.py tests/unit/test_coordinator.py tests/unit/test_coordinator_buffer_update.py tests/unit/test_util.py tests/integration/test_refresh_cycle.py tests/integration/test_slot_concurrency.py -q` against the listed test files

**Checkpoint**: Caller verification proves FR-004, FR-005, FR-006, SC-005, and
SC-006, including util-level hidden patch seams and visible event-overrides and
coordinator patch paths.

---

## Phase 7: Maintainability, File Sizes, and Aislop Gates (Priority: P2)

**Goal**: Resolve the active utility complexity findings without suppressions and
without creating a new catch-all module.

**Independent Test**: Measure every in-scope file immediately after final shell
wiring and run existing complexity tooling with no new utility ignore directive.

### Cleanup and Complexity Gates

- [x] T071 [US5] Confirm final implementation diff is limited to `custom_components/rental_control/util.py`, `custom_components/rental_control/helpers.py`, `custom_components/rental_control/keymaster_services.py`, `custom_components/rental_control/state_handlers.py`, intentional wrapper edits in `custom_components/rental_control/event_overrides.py` and `custom_components/rental_control/coordinator.py`, and directly required test files
- [x] T072 [US5] Remove temporary extraction shims from `custom_components/rental_control/util.py`, `helpers.py`, `keymaster_services.py`, and `state_handlers.py`, leaving only planned wrappers, helper exports, request/dependency objects, and internal helper functions
- [x] T073 [US5] Confirm no new lock-code business rules, state-change semantics, service calls, sensors, configuration options, Store authority, Home Assistant state writes, coordinator refreshes, reconciliation launches, blocking I/O, diagnostics fields, or user-visible delays were introduced in in-scope utility files
- [x] T074 [US5] Measure `custom_components/rental_control/util.py`, `helpers.py`, `keymaster_services.py`, and `state_handlers.py` with `wc -l` and confirm every utility-related file is below 400 lines
- [x] T075 [US5] Ensure every project-owned function in `custom_components/rental_control/util.py`, `helpers.py`, `keymaster_services.py`, and `state_handlers.py` is below 80 lines, splitting per-lock-type and staged helper functions without changing behavior where needed
- [x] T076 [US5] Ensure every project-owned parameter list in `custom_components/rental_control/util.py`, `helpers.py`, `keymaster_services.py`, and `state_handlers.py` has no more than six parameters unless an external framework signature requires otherwise
- [x] T077 [US5] Verify no `aislop-ignore-file` directive was added to `custom_components/rental_control/util.py`, `helpers.py`, `keymaster_services.py`, or `state_handlers.py` and no replacement complexity suppression hides file-size or function-length findings
- [x] T078 [US5] Run isolated complexity validation with `uv run pre-commit run aislop` and confirm file-size, function-length, and parameter-count thresholds pass for the utility decomposition

**Checkpoint**: Maintainability proves FR-015, FR-016, FR-017, FR-018,
FR-019, SC-007, SC-008, and SC-009.

---

## Phase 8: Polish & Cross-Cutting Acceptance Gates

**Purpose**: Verify behavior parity, caller compatibility, quality gates,
traceability, and documentation of validation results.

### Acceptance and Quality Gates

- [x] T079 Run unchanged utility and caller parity tests with `uv run pytest tests/unit/test_util.py tests/unit/test_event_overrides.py tests/unit/test_event_overrides_apply.py tests/unit/test_coordinator.py tests/unit/test_coordinator_buffer_update.py tests/integration/test_refresh_cycle.py tests/integration/test_slot_concurrency.py -x -q` against the listed test files
- [x] T080 Run all new focused helper tests with `uv run pytest tests/unit/test_keymaster_services.py tests/unit/test_state_handlers.py -q` against the listed test files
- [x] T081 Run production-consumer coverage with `uv run pytest tests/unit/test_sensors.py tests/unit/test_calendar.py tests/unit/test_switch.py tests/integration/test_refresh_cycle.py -x -q` against the listed test files where present in the implementation branch
- [x] T082 Run full regression tests with `uv run pytest tests/ -x -q` against `tests/`
- [x] T083 Run linting with `uv run ruff check custom_components/ tests/` against `custom_components/` and `tests/`
- [x] T084 Run full pre-commit validation with `uv run pre-commit run --all-files` against repository-tracked files, including reuse, yamllint, actionlint, aislop, ruff, ruff-format, mypy, interrogate, and gitlint hooks
- [x] T085 Verify every FR-001 through FR-019 has a test, implementation, or acceptance task mapped in `specs/018-decompose-util/tasks.md`
- [x] T086 Verify every SC-001 through SC-010 has a test, implementation, or acceptance task mapped in `specs/018-decompose-util/tasks.md`
- [x] T087 Review `specs/018-decompose-util/quickstart.md` and confirm the implementation PR notes list unchanged parity commands, new focused helper commands, util/event_overrides/coordinator patch-target results, caller-import verification, hot-path safeguards, file-size measurements, final `aislop` results, full `pytest tests/ -x -q`, ruff, and pre-commit results

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup & Baseline (Phase 1)**: No dependencies.
- **Generic Helpers Extraction (Phase 2)**: Depends on Setup and must complete
  before Keymaster services and state handlers import generic helper behavior.
- **Keymaster Service Helpers (Phase 3)**: Depends on helper extraction because
  service builders use `OperationResult`, `add_call`, `check_gather_results`,
  `apply_buffer`, text-state helpers, datetime coercion, and `trim_name`.
- **State-Change Handler (Phase 4)**: Depends on helper extraction because state
  normalization, datetime parsing, and trim/prefix restoration use generic
  helper semantics.
- **Util Shell Re-Exports (Phase 5)**: Depends on helper, service, and state
  modules being present so `util.py` can become the public shell with runtime
  wrappers.
- **Caller Imports and Patch Targets (Phase 6)**: Depends on util shell wrappers
  and verifies event-overrides and coordinator wrappers after shell delegation.
- **Maintainability (Phase 7)**: Depends on all extraction, wrapper wiring,
  caller verification, and shim cleanup. File-size measurement and `aislop`
  checks happen only after the final module split is in place.
- **Polish (Phase 8)**: Depends on all desired extraction, wrapper,
  compatibility, and cleanup phases.

### User Story Dependencies

- **US1 (P1)**: Keymaster service safety starts after generic helpers and must
  complete before final event-overrides/coordinator caller validation.
- **US2 (P1)**: State-change handling starts after generic helpers and must
  complete before the util shell delegates `handle_state_change`.
- **US3 (P1)**: Public util compatibility depends on helper/service/state modules
  and completes after caller and patch-target verification.
- **US4 (P1)**: Generic helper parity is the first extraction step and feeds the
  other behavior-preserving splits.
- **US5 (P2)**: Maintainability follows US1-US4 because file/function thresholds
  are meaningful only after the shell and wrappers are slimmed.

### Within Each Story

- Focused tests are written before the corresponding extraction tasks and should
  fail or expose missing coverage until the extraction lands.
- `helpers.py` precedes `keymaster_services.py` and `state_handlers.py`.
- `KeymasterServiceDeps` and `StateHandlerDeps` read util module attributes at
  wrapper call time, not at import time.
- `util.async_fire_*`, `util.get_event_identities`, `util.get_event_names`, and
  `util.handle_state_change` remain thin runtime wrappers, not copied aliases.
- `event_overrides.py` keeps module-level wrappers used through
  `EventOverrides._module`; unpatched wrappers call `util.<name>` at runtime.
- `coordinator.py` keeps module-level compatibility wrappers; unpatched
  `async_fire_clear_code` calls `util.async_fire_clear_code` at runtime.
- Production caller import verification happens after wrapper wiring and before
  maintainability cleanup.
- File-size measurement and `uv run pre-commit run aislop` happen after temporary
  shims are removed and before final full gates.
- No utility `aislop-ignore-file` directive may be added at any point.

---

## Parallel Opportunities

- T012 through T015 can be developed in parallel after Phase 1 if each
  contributor owns distinct helper test sections in `tests/unit/test_util.py`.
- T024, T025, and T026 can be developed in parallel after `KeymasterServiceDeps`
  tests because set, clear, and update-times scenarios own separate helper paths.
- T038 through T043 can be developed in parallel after state-handler fixtures are
  stable because context, reset/suppression, snapshots, datetimes, trim, and
  dispatch tests cover separate callback stages.
- T062 through T066 can be developed in parallel after util shell wrappers exist
  because event-overrides, coordinator, and import-boundary tests touch different
  patch surfaces.
- T079, T080, and T081 can run independently once implementation is complete;
  T082 through T084 are final serial quality gates.

## Parallel Example: Wrapper Verification After Shell Delegation

```bash
Task: "Add util import-surface tests in tests/unit/test_util.py"
Task: "Add event_overrides wrapper patch tests in tests/unit/test_event_overrides.py"
Task: "Add coordinator wrapper patch tests in tests/unit/test_coordinator.py"
```

---

## Implementation Strategy

### MVP First (Physical Lock Safety and Patch Seams)

1. Complete Phase 1 baselines.
2. Extract generic helpers first so Keymaster and state-handler modules share the
   exact current text-state, buffer, naming, and result semantics.
3. Extract Keymaster service helpers with focused parity tests for ordered set,
   clear, and update-times calls.
4. Extract state-change handling with focused parity tests for every conservative
   early return and final override update.
5. Convert `util.py` to thin runtime wrappers and immediately verify util-level,
   event-overrides-level, and coordinator-level patch seams.

### Incremental Delivery

1. Build `helpers.py` and keep observed generic helper behavior unchanged.
2. Build `keymaster_services.py` with per-operation phase helpers below 80 lines.
3. Build `state_handlers.py` with staged context, snapshot, normalize, parse,
   restore, and dispatch helpers below 80 lines.
4. Slim `util.py` into the compatibility shell with the full import surface and
   runtime wrappers for every patch-sensitive seam.
5. Update only intentional `event_overrides.py` and `coordinator.py` wrappers to
   call the `util` module at runtime and verify production caller imports.
6. Remove temporary shims, measure every utility-related file below 400 lines,
   run `aislop` with no suppression, and then run full pytest, ruff, and
   pre-commit gates.

---

## Acceptance Coverage Map

| Coverage item | Primary tasks |
|---------------|---------------|
| US1 Keymaster service safety | T006, T010, T023-T036, T062-T063, T070, T079-T080 |
| US2 state-change handling | T007, T010, T037-T050, T058, T070, T079-T080 |
| US3 public util compatibility | T008-T009, T051-T070, T079, T085 |
| US4 generic helper semantics | T005, T012-T022, T056, T079-T080 |
| US5 maintainability under aislop | T011, T021, T035, T049, T071-T078, T084 |
| FR-001 observable behavior unchanged | T010, T022, T036, T050, T061, T070, T079-T084 |
| FR-002 existing tests unchanged | T010, T022, T036, T050, T061, T070, T079, T082 |
| FR-003 util import surface retained | T008, T051-T061, T066, T069-T070, T085 |
| FR-004 util async_fire patch seams | T023, T053, T055, T057, T062-T063, T070 |
| FR-005 util event identity patch seam | T014, T053-T054, T059, T062-T063, T070 |
| FR-006 event_overrides/coordinator patch targets | T009, T062-T068, T070 |
| FR-007 set-code equivalence | T024, T028-T029, T032-T036, T079-T080 |
| FR-008 clear-code equivalence | T025, T028, T030, T032-T036, T079-T080 |
| FR-009 update-times equivalence | T026, T028, T031-T036, T079-T080 |
| FR-010 handle_state_change equivalence | T037-T050, T058, T079-T080 |
| FR-011 text-state helper semantics | T012, T017, T022, T047, T079 |
| FR-012 apply_buffer semantics | T013, T019, T022, T029, T031, T079 |
| FR-013 event and slot naming helpers | T014, T019, T022, T029, T048, T079 |
| FR-014 generic helper behavior | T015, T018-T020, T022, T079 |
| FR-015 file/function/parameter limits | T011, T021, T035, T049, T071-T078, T084 |
| FR-016 no utility aislop suppression | T011, T077-T078, T084 |
| FR-017 coherent concern decomposition | T016, T028, T044, T056-T060, T071-T073 |
| FR-018 no new hot-path side effects | T020, T034, T046, T070, T073, T079-T084 |
| FR-019 behavior-preserving documentation | T002-T004, T073, T085-T087 |
| SC-001 existing util-related tests green | T010, T079, T082 |
| SC-002 Keymaster service parity | T023-T036, T062-T063, T070, T079-T080 |
| SC-003 state-change parity | T037-T050, T058, T079-T080 |
| SC-004 helper parity | T012-T022, T079-T080 |
| SC-005 util import compatibility | T051-T061, T066, T069-T070, T085 |
| SC-006 monkeypatch seams preserved | T009, T023, T037, T053-T055, T062-T068, T070 |
| SC-007 complexity thresholds | T021, T035, T049, T074-T078, T084 |
| SC-008 no complexity directive | T011, T077-T078, T084 |
| SC-009 no added hot-path work | T034, T046, T073, T079-T084 |
| SC-010 docs-only tasks stage | This `tasks.md` PR only; implementation tasks start unchecked |
