<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Research: Decompose Config Flow

## Decision: Keep `config_flow.py` as the Home Assistant shell

**Rationale**: Home Assistant discovers the integration through
`custom_components.rental_control.config_flow`, and the current source defines
`RentalControlFlowHandler` as the `ConfigFlow` subclass with `domain=DOMAIN`,
`VERSION = 10`, `async_step_user`, and the `@callback` options-flow factory.
Visible tests also import `RentalControlFlowHandler`, import
`_normalize_lock_entry`, and patch
`custom_components.rental_control.config_flow.gen_uuid`. Keeping this module as
the shell preserves discovery, imports, and monkeypatch seams while allowing the
long schema and validation bodies to move out.

**Alternatives considered**:

- Convert `config_flow.py` into a package: rejected because package conversion adds
  Home Assistant discovery and import risk for no behavior benefit.
- Move flow classes into helper modules and re-export them: rejected because class
  registration and hidden test expectations are most safely preserved by leaving
  the Home Assistant subclasses in place.

## Decision: Add `config_flow_helpers/` as an internal sibling package

**Rationale**: The current file mixes Home Assistant subclass shells, schema
construction, URL fetching, scalar validation, lock conversion, code-generator
conversion, and form rendering. A helper package beside `config_flow.py` lets
schema, validation, model, and transition concerns remain focused and below the
active file/function thresholds without creating public API.

**Alternatives considered**:

- Add one large `config_flow_helpers.py`: rejected because it risks recreating a
  mixed-concern file near the same thresholds.
- Place helpers under top-level `helpers/`: rejected because these helpers are
  specific to config-flow behavior and should not appear as general project API.

## Decision: Preserve `config_flow.gen_uuid` by keeping unique ID generation in the shell

**Rationale**: Duplicate detection calls `gen_uuid(self.created)` through the
module-level name in `config_flow.py`, and visible tests patch that exact path.
If validation helpers import `gen_uuid` directly, the patch would no longer affect
unique ID generation. Keeping `_get_unique_id()` on `RentalControlFlowHandler`
and calling the module global at runtime preserves the current seam.

**Alternatives considered**:

- Move `_get_unique_id()` into `validation.py`: rejected because direct helper
  imports can accidentally bypass the config-flow module patch.
- Pass a generated UUID value into validation: rejected because it changes the
  timing and ownership of Home Assistant duplicate detection.

## Decision: Extract `_get_schema` into per-concern schema builders

**Rationale**: `_get_schema` is 151 lines because it handles defaults,
lock-manager display conversion, base fields, selectors, code fields, trim-name
fields, and options-only fields in one function. Splitting these into focused
builders keeps each field group declarative, makes selector/default parity easier
to test, and resolves the function-length finding without changing the returned
`vol.Schema`.

**Alternatives considered**:

- Move the entire `_get_schema` body wholesale to `schemas.py`: rejected because it
  would leave the function-length finding unresolved.
- Generate schema fields dynamically from metadata tables: rejected for the first
  decomposition because it risks subtle differences in `vol.Required`,
  `vol.Optional`, defaults, and selector objects.

## Decision: Extract validation while preserving mutation timing

**Rationale**: `_start_config_flow` is 147 lines and combines orchestration with
validation details. The implementation can split URL checks, time checks, numeric
bounds, code-length rules, trim-name rules, generator conversion, and successful
lock conversion into small helpers. The step helper remains responsible for
calling those helpers in the current order so error precedence and user-input
mutation timing remain identical.

**Alternatives considered**:

- Use a new voluptuous schema for submitted-data validation: rejected because it
  could change error keys, ordering, or how mutated values are preserved on a
  re-render.
- Defer code-generator conversion until after all errors are known: rejected
  because current behavior mutates the submitted description to its type before
  re-rendering on later errors.

## Decision: Use `ConfigFormContext` to reduce `_show_config_form`

**Rationale**: `_show_config_form` has seven parameters because step ID, user
input, errors, description placeholders, defaults, and entry ID are passed
separately. Grouping those values into `ConfigFormContext` reduces the helper to
one flow object plus one context while preserving the same `async_show_form`
result. Both current call sites are internal to `_start_config_flow` and can be
updated together.

**Alternatives considered**:

- Leave the seven-parameter helper unchanged: rejected because it leaves the
  active parameter-count finding unresolved.
- Inline `async_show_form` at both call sites: rejected because it duplicates the
  schema-building call and weakens the importable helper seam.
- Use a plain dictionary context: rejected because a typed dataclass is clearer
  and easier to test for required fields.

## Decision: Keep `_start_config_flow` importable as a compatibility wrapper

**Rationale**: The feature spec preserves current helper import seams, including
`_start_config_flow`. The function already has no more than six parameters, so it
can remain in `config_flow.py` as a small wrapper that delegates to
`config_flow_helpers.steps`. This keeps hidden direct imports working while moving
the long body out of the shell.

**Alternatives considered**:

- Rename `_start_config_flow` to a public helper in the new package: rejected
  because hidden tests or downstream checks may import the current name.
- Keep the full body in `config_flow.py`: rejected because it leaves the
  function-length and file-size findings unresolved.

## Decision: Omit contracts and agent-context updates

**Rationale**: This is an internal, behavior-preserving refactor plan. It adds no
external API, service schema, entity contract, event payload, runtime dependency,
language, framework, or tool. Existing Home Assistant-visible behavior and Python
import surfaces are preserved rather than extended.

**Alternatives considered**:

- Add contract files for internal dataclasses: rejected because `data-model.md`
  and `plan.md` already define those internal structures.
- Run `update-agent-context.sh`: rejected because no new technology or dependency
  is introduced.
