<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Implementation Plan: Decompose Config Flow

**Feature**: `020-decompose-config-flow` | **Planning Branch**:
`020-decompose-config-flow-plan` | **Date**: 2026-06-29 | **Spec**:
[spec.md](spec.md)
**Input**: Feature specification from
`specs/020-decompose-config-flow/spec.md` and GitHub issue #573

## Summary

Decompose `custom_components/rental_control/config_flow.py` without changing
Home Assistant-visible behavior. The current 549-line module is the
compatibility shell for initial setup, options editing, config-entry versioning,
schema construction, URL and input validation, lock-manager conversion,
code-generator conversion, duplicate detection, and form rendering.

The implementation will keep `config_flow.py` as the Home Assistant flow shell
that owns `RentalControlFlowHandler`, `RentalControlOptionsFlow`, `VERSION = 10`,
`async_step_user`, `async_step_init`, and the `@callback` options-flow factory.
Focused helpers under a new sibling package,
`custom_components/rental_control/config_flow_helpers/`, will own declarative
schema building, validation, step transition decisions, and grouped form context.
The current test-consumed seams remain importable from
`custom_components.rental_control.config_flow`: `_normalize_lock_entry`,
`_get_schema`, `_show_config_form`, `_start_config_flow`, the flow classes, and
the module-level `gen_uuid` monkeypatch seam.

## Technical Context

**Language/Version**: Python >=3.14.2
**Primary Dependencies**: Home Assistant runtime >=2026.4.0 per `hacs.json`;
dev/test dependency `homeassistant>=2026.6.0` per `pyproject.toml`;
`pytest-homeassistant-custom-component`, `aioresponses`, `icalendar>=7.0.0`,
`x-wr-timezone>=2.0.0`, and `voluptuous`
**Storage**: Home Assistant config-entry data only; this refactor adds no
persistent storage and preserves existing setup/options entry data keys
**Testing**: `uv run pytest tests/unit/test_config_flow.py`; broader caller smoke
coverage through `uv run pytest tests/integration/test_full_setup.py
 tests/integration/test_refresh_cycle.py`; ruff via `uv run ruff check
 custom_components/ tests/`; pre-commit hooks for reuse, ruff, mypy,
interrogate, yamllint, actionlint, and gitlint
**Target Platform**: Home Assistant custom integration on the HA asyncio event
loop for Linux, HA OS, Docker, and HACS-managed installs
**Project Type**: Single Home Assistant custom integration
**Performance Goals**: Config and options flows perform the same schema
construction, validation, single calendar URL fetch on submitted data, and
entry creation/update with no extra network calls, config-entry writes, async
tasks, Home Assistant state writes, or user-visible delays
**Constraints**: Documentation-only PLAN PR; no production code. Runtime
implementation must preserve flow registration, step IDs `user` and `init`,
`VERSION = 10`, `async_get_options_flow`, schema keys/defaults/selectors,
validation error keys, duplicate handling, URL fetch behavior, entry data,
options data loading, import seams, and the `config_flow.gen_uuid` patch seam.
**Scale/Scope**: One 549-line module becomes a small Home Assistant shell plus a
helper package. Current measured debt is file size, `_get_schema` at 151 lines,
`_start_config_flow` at 147 lines, and `_show_config_form` with seven parameters.
Implementation target: every config-flow-related file below 400 lines, every
project-owned function below 80 lines, and every project-owned parameter list no
more than six parameters. Keep the existing
`# aislop-ignore-file ai-slop/hallucinated-import -- Provided by Home Assistant runtime.`
directive and add no complexity suppression.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I: Code Quality & Testing | PASS | Plan requires existing config-flow tests unchanged, targeted helper parity tests, import smoke checks, ruff, and complexity measurement before implementation completion. |
| II: Atomic Commit Discipline | PASS | This PR is one docs-only PLAN commit. Future implementation can split schema helpers, validation helpers, shell wiring, compatibility wrappers, and tests into atomic commits. |
| III: Licensing & Attribution | PASS | New markdown artifacts include SPDX headers. Future Python helper modules must include project SPDX headers and public docstrings. |
| IV: Pre-Commit Integrity | PASS | No hook bypass is planned. Quickstart defines local validation before implementation merge. |
| V: Agent Co-Authorship & DCO | PASS | The PLAN commit uses `git commit -s` and the requested Claude co-author trailer. |
| VI: User Experience Consistency | PASS | Form fields, selectors, defaults, errors, step IDs, entry titles, entry data, options data, and import seams are explicitly preserved. |
| VII: Performance Requirements | PASS | Helpers are pure except the existing submitted-data URL fetch; the shell adds no extra I/O, state writes, config-entry writes, tasks, or delays. |

**Gate result: PASS** — no violations. Proceeding to Phase 0.

## Project Structure

### Documentation (this feature)

```text
specs/020-decompose-config-flow/
├── plan.md                    # This file
├── research.md                # Phase 0 decisions and alternatives
├── data-model.md              # Phase 1 helper entities and ownership
├── quickstart.md              # Phase 1 parity validation guide
└── tasks.md                   # Phase 2 output only; not created in PLAN stage
```

`contracts/` is intentionally omitted. This behavior-preserving refactor adds
no external HTTP API, WebSocket API, Home Assistant service, entity-service,
event payload, or new public Python API contract. Internal dataclasses and
helper boundaries are specified in this plan and in [data-model.md](data-model.md).

### Source Code (repository root)

```text
custom_components/rental_control/
├── config_flow.py                         # Public HA flow shell; flow classes,
│                                         # VERSION, async_step_* methods,
│                                         # async_get_options_flow, gen_uuid seam,
│                                         # and compatibility wrappers remain
├── config_flow_helpers/
│   ├── __init__.py                        # Internal package marker/typed exports
│   ├── models.py                          # ConfigFormContext,
│   │                                     # SchemaBuildContext,
│   │                                     # FlowValidationResult,
│   │                                     # URLValidationResult
│   ├── schemas.py                         # Per-concern schema field builders,
│   │                                     # defaults normalization, lock choices,
│   │                                     # generator choices, options-only fields
│   ├── validation.py                      # URL checks, time checks, numeric
│   │                                     # bounds, code length, trim-name rules,
│   │                                     # lock/generator successful conversion
│   └── steps.py                           # Submitted-data orchestration and
│                                         # entry/form transition helpers
└── const.py                               # Existing constants reused unchanged

tests/
└── unit/
    ├── test_config_flow.py                # Existing behavior oracle unchanged
    ├── test_config_flow_schemas.py        # Focused schema/default parity tests
    └── test_config_flow_validation.py     # Focused validation/conversion parity
```

**Structure Decision**: Keep `config_flow.py` as a module, not a package, because
Home Assistant discovers the flow through that module and visible tests import
from it directly. A sibling `config_flow_helpers/` package gives focused
extraction points while leaving the stable flow and monkeypatch module unchanged.
No production caller imports from `config_flow_helpers/`.

## Concrete Decomposition Design

### Public compatibility boundary

`custom_components.rental_control.config_flow` remains the only Home
Assistant-facing config-flow module. These names stay importable and effective at
that module path:

- `RentalControlFlowHandler`
- `RentalControlOptionsFlow`
- `gen_uuid`
- `_normalize_lock_entry`
- `_get_schema`
- `_show_config_form`
- `_start_config_flow`

`RentalControlFlowHandler` remains the `config_entries.ConfigFlow` subclass with
`domain=DOMAIN`, `VERSION = 10`, `async_step_user`, and the
`@callback`-decorated `async_get_options_flow`. `RentalControlOptionsFlow`
remains the `config_entries.OptionsFlow` subclass with `async_step_init` and the
same config-entry data loading behavior.

`RentalControlFlowHandler._get_unique_id()` stays in `config_flow.py` and calls
the module-level `gen_uuid` name at runtime:

```python
gen_uuid(self.created)
```

That preserves tests that patch
`custom_components.rental_control.config_flow.gen_uuid`. Validation helpers must
not import `gen_uuid` directly or cache it in a default argument.

### Ground-truth source and test analysis

The implementation must start from live `origin/main`, not the issue summary
alone. Current source facts captured during planning:

- `config_flow.py` is 549 lines.
- The only current Aislop directive is
  `# aislop-ignore-file ai-slop/hallucinated-import -- Provided by Home Assistant runtime.`
- `_get_schema` is 151 lines and owns defaults merging, lock-entry display
  conversion, base setup fields, selector construction, and options-only fields.
- `_start_config_flow` is 147 lines and owns lock-entry normalization, duplicate
  detection, URL validation/fetch, scalar validation, code-generator conversion,
  trim-name validation, successful lock conversion, creation timestamp insertion,
  generated-flag insertion, entry creation, and form re-rendering.
- `_show_config_form` has seven parameters: `cls`, `step_id`, `user_input`,
  `errors`, `description_placeholders`, `defaults`, and `entry_id`.
- The two current call sites are the error path around `config_flow.py:531` and
  the initial-form path around `config_flow.py:541`; both will be updated to pass
  a `ConfigFormContext` instead of separate form values.
- Visible tests import `_normalize_lock_entry`, import
  `RentalControlFlowHandler` to assert `VERSION == 10`, patch
  `custom_components.rental_control.config_flow.gen_uuid`, and exercise config
  and options flows through Home Assistant's flow manager.

### Flow shell responsibilities

`config_flow.py` keeps responsibilities that require Home Assistant inheritance,
flow-manager callbacks, or module-path compatibility:

1. class declarations and domain registration;
2. `VERSION = 10`;
3. `RentalControlFlowHandler.DEFAULTS`;
4. `RentalControlFlowHandler.__init__()` and `created` timestamp;
5. `_get_unique_id()` with runtime lookup of module-level `gen_uuid`;
6. `async_step_user()` and `async_step_init()` step methods;
7. `async_get_options_flow()` with `@callback`;
8. module-level compatibility wrappers for `_normalize_lock_entry`, `_get_schema`,
   `_show_config_form`, and `_start_config_flow`.

The shell delegates schema construction, submitted-data validation, and transition
decisions to helpers. Helpers may receive the live flow object when they need
`hass`, `async_show_form`, or `async_create_entry`, but Home Assistant-facing
method names and class inheritance remain in `config_flow.py`.

### `models.py`

`models.py` owns small typed values used to reduce parameter counts and make
validation outputs explicit:

- `ConfigFormContext`: `step_id`, `user_input`, `errors`,
  `description_placeholders`, `defaults`, and `entry_id`.
- `SchemaBuildContext`: `hass`, `user_input`, normalized defaults, and `entry_id`.
- `URLValidationResult`: URL field error, HTTP status reason for logging, and
  content-type outcome.
- `FlowValidationResult`: mutated input, errors, description placeholders, and a
  boolean indicating whether an entry can be created.

All models are internal implementation aids, not new public API. They must have
project SPDX headers, type hints, and docstrings.

### `schemas.py`

`schemas.py` owns `_get_schema` behavior through focused helpers, each below the
function-length threshold:

1. normalize `None` lock-entry defaults by omitting the field default;
2. convert stored lock entity IDs to displayed lock-manager titles for defaults;
3. create the `_get_default` lookup callable with current user-input precedence;
4. build required identity and URL fields;
5. build refresh/timezone/event-prefix/check-in/check-out/day fields;
6. build lock selector fields using the same `(none)` sentinel and lock-manager
   titles;
7. build slot/code/code-generator/update-code fields;
8. build honor-event-times, ignore-non-reserved, SSL, cleaning-window, trim-name,
   and max-name-length fields;
9. extend options-only diagnostics and code-buffer fields when `entry_id` is not
   `None`;
10. return a `vol.Schema(..., extra=ALLOW_EXTRA)` exactly as today.

`config_flow._get_schema()` remains importable as a wrapper over the helper. The
wrapper keeps current call semantics for `hass`, `user_input`, `default_dict`,
and `entry_id` while the long schema body moves to per-concern builders.

### `validation.py`

`validation.py` owns validation and conversion concerns currently embedded in
`_start_config_flow`:

- `_normalize_lock_entry` behavior for `None`, empty strings, whitespace, and
  existing values;
- URL syntax validation with `cv.url()` and the same `vol.Invalid` logging;
- HTTPS-required validation when SSL verification is enabled;
- existing `async_get_clientsession(..., verify_ssl=...)` fetch, timeout, status,
  reason logging, and `text/calendar` content-type check;
- refresh-frequency bounds of `0..1440`;
- check-in and checkout `cv.time()` validation and `bad_time` error keys;
- days and max-events minimum validation;
- code-length minimum and even-number validation;
- code-generator description-to-type conversion at the same point in the flow;
- max-name-length minimum validation;
- trim-name prefix boundary validation using `MIN_NAME_LENGTH` and the same base
  error;
- successful lock-entry conversion from `(none)` to `None` or lock-manager title
  to entity ID;
- successful insertion of `CONF_CREATION_DATETIME` and `CONF_GENERATE`.

Error precedence and mutation timing must match the current implementation. In
particular, code-generator conversion still happens before re-rendering on later
validation errors, while lock-entry entity conversion happens only when there are
no errors.

### `steps.py`

`steps.py` owns short orchestration helpers for submitted data and form
transition decisions. `_start_config_flow` remains importable from
`config_flow.py` with the current six-parameter signature and delegates to this
module.

The helper sequence is:

1. create empty `errors` and `description_placeholders` dictionaries;
2. when `user_input is None`, build a `ConfigFormContext` and show the form;
3. normalize `CONF_LOCK_ENTRY` before schema/form rendering;
4. call the shell's `_get_unique_id()` only when the flow object provides it;
5. run validation helpers in the current order;
6. on errors, build a `ConfigFormContext` and show the form;
7. on success, apply successful conversions and call `async_create_entry()` with
   the same title and data.

The helper must not add a flow step, change step IDs, change entry titles, write
config entries outside `async_create_entry`, or perform any additional URL fetch.

### `_show_config_form` parameter reduction

Implementation introduces `ConfigFormContext` and changes the primary helper
signature to no more than six declared parameters, preferably:

```python
def _show_config_form(
    cls: RentalControlFlowHandler | RentalControlOptionsFlow,
    context: ConfigFormContext,
) -> Any:
    ...
```

Both current call sites in `_start_config_flow` are updated:

- the error re-render path around current line 531 passes a context containing the
  submitted `user_input`, accumulated `errors`, placeholders, defaults, and
  `entry_id`;
- the initial-form path around current line 541 passes a context containing
  `user_input=None`, empty errors/placeholders, defaults, and `entry_id`.

If hidden tests call `_show_config_form` directly with the legacy expanded
arguments, the implementation may add a small compatibility adapter that accepts
a grouped context and normalizes legacy arguments while still declaring no more
than six parameters. New internal calls should use `ConfigFormContext` only.

### `aislop` directive handling

The implementation must keep the legitimate Home Assistant runtime import
suppression in `config_flow.py`:

```python
# aislop-ignore-file ai-slop/hallucinated-import -- Provided by Home Assistant runtime.
```

There is no current complexity directive in `config_flow.py`; the implementation
must not add `aislop-ignore`, `aislop-ignore-file`, or equivalent suppressions for
file size, function length, or parameter count. Complexity findings are resolved
by splitting helpers and grouping form context.

## Phase 0 Research Output

See [research.md](research.md). All planning questions are resolved; no open clarifications remain.

## Phase 1 Design Output

See [data-model.md](data-model.md) for internal helper entities and
[quickstart.md](quickstart.md) for the implementation validation guide. No
contracts are generated because this refactor introduces no external API, service,
event, entity, or public caller contract. Agent-context updates are intentionally
omitted because no new language, framework, runtime dependency, package manager,
or tool is introduced.

## Post-Design Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I: Code Quality & Testing | PASS | Design keeps HA shells and compatibility wrappers testable and adds focused schema and validation parity coverage around the unchanged config-flow oracle. |
| II: Atomic Commit Discipline | PASS | PLAN artifacts are one docs-only change; future implementation can be split into small extraction and test commits. |
| III: Licensing & Attribution | PASS | `plan.md`, `research.md`, `data-model.md`, and `quickstart.md` include SPDX headers. Future Python helpers must do the same. |
| IV: Pre-Commit Integrity | PASS | The PR must pass hooks and CI without bypass flags. |
| V: Agent Co-Authorship & DCO | PASS | The planned commit uses sign-off and the requested Claude co-author trailer. |
| VI: User Experience Consistency | PASS | Flow classes, step IDs, schema, defaults, selectors, errors, entry data, options data, and import/patch seams are preserved. |
| VII: Performance Requirements | PASS | Extracted helpers are in-memory around the same submitted-data URL fetch and add no new I/O, writes, tasks, or delays. |

**Gate result: PASS** — no plan-stage constitution violations.

## Complexity Tracking

No constitutional violations require justification. Existing config-flow
complexity debt remains the implementation target. If `config_flow.py` or any
helper approaches 400 lines or a helper approaches 80 lines during implementation,
split by coherent concern instead of adding any suppression.

## Phase Notes

- PLAN stage stops here. Do not create `tasks.md` or modify production code in
  this PR.
- Implementation must treat current `origin/main` `config_flow.py` as truth.
  Planning shorthand and issue text are secondary when they disagree with source.
- Keep the refactor behavior-preserving. Any discovered behavior bug or business
  rule improvement belongs in a separate issue/feature, not this decomposition.
