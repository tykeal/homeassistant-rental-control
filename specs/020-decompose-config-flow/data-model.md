<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Data Model: Decompose Config Flow

This feature is a behavior-preserving refactor. The models below are internal
implementation aids, not new Home Assistant options or public API.
`RentalControlFlowHandler`, `RentalControlOptionsFlow`, and current helper seams
remain importable from `custom_components.rental_control.config_flow`.

## Existing public and compatibility entities retained on `config_flow.py`

### RentalControlFlowHandler

**Owner module**: `custom_components.rental_control.config_flow`

**Fields/state retained**:

- `VERSION = 10`
- `DEFAULTS`
- `created`
- inherited Home Assistant flow state

**Relationships**:

- extends `config_entries.ConfigFlow` with `domain=DOMAIN`
- owns `async_step_user()`
- owns `@callback async_get_options_flow()`
- creates `RentalControlOptionsFlow`
- calls `_start_config_flow()` compatibility wrapper
- calls module-level `gen_uuid` from `_get_unique_id()` at runtime

**Validation rules**:

- Initial step ID remains `user`.
- Created entry title remains the submitted calendar name.
- Duplicate detection still maps a conflicting unique ID to `{CONF_NAME:
  "same_name"}`.
- `gen_uuid` remains patchable at
  `custom_components.rental_control.config_flow.gen_uuid`.

### RentalControlOptionsFlow

**Owner module**: `custom_components.rental_control.config_flow`

**Fields/state retained**:

- inherited `config_entry`
- existing config-entry data copied into `flow_data`

**Relationships**:

- extends `config_entries.OptionsFlow`
- owns `async_step_init()`
- calls `_start_config_flow()` compatibility wrapper with step ID `init`
- passes `self.config_entry.entry_id` so options-only fields are included

**Validation rules**:

- Options step ID remains `init`.
- Existing `config_entry.data` remains the default source.
- Options flow saves the same validated data through `async_create_entry()`.

### ConfigFlowCompatibilitySurface

**Owner module**: `custom_components.rental_control.config_flow`

**Names retained**:

- `RentalControlFlowHandler`
- `RentalControlOptionsFlow`
- `gen_uuid`
- `_normalize_lock_entry`
- `_get_schema`
- `_show_config_form`
- `_start_config_flow`

**Validation rules**:

- Imports from the current module path continue to work.
- Wrappers delegate to helper modules without changing observable behavior.
- Module globals that tests patch, especially `gen_uuid`, are not bypassed by
  helper-level imports or cached aliases.

## New internal helper entities

### ConfigFormContext

**Owner module**: `config_flow_helpers.models`

**Purpose**: Group form-rendering inputs so `_show_config_form` declares no more
than six parameters.

**Fields**:

- `step_id: str`
- `user_input: dict[str, Any] | None`
- `errors: dict[str, str]`
- `description_placeholders: dict[str, str]`
- `defaults: dict[str, Any] | None`
- `entry_id: str | None`

**Relationships**:

- Constructed by `_start_config_flow` or `steps.py` for both current form call
  sites.
- Consumed by `_show_config_form()` to call `_get_schema()` and
  `async_show_form()`.

**Validation rules**:

- Error and placeholder dictionaries are the same objects/values the current flow
  would pass to `async_show_form()`.
- `entry_id is not None` continues to mean options-only schema fields are added.
- The error re-render path preserves submitted `user_input`; the initial render
  passes `None` exactly as today.

### SchemaBuildContext

**Owner module**: `config_flow_helpers.models` or `schemas.py`

**Purpose**: Explicit values needed to construct the current `vol.Schema`.

**Fields**:

- `hass: HomeAssistant`
- `user_input: dict[str, Any]`
- `defaults: dict[str, Any] | None`
- `entry_id: str | None`

**Relationships**:

- Produced by `config_flow._get_schema()` wrapper.
- Consumed by field-group builders in `schemas.py`.

**Validation rules**:

- Empty `user_input` is used when the caller passes `None`.
- Lock-entry defaults of `None` are omitted before default lookup.
- Stored lock entity defaults are converted to lock-manager titles for display.
- Returned schema uses `extra=ALLOW_EXTRA`.

### SchemaFieldGroups

**Owner module**: `config_flow_helpers.schemas`

**Purpose**: Focused schema dictionaries that compose into the current form
schema.

**Field groups**:

- identity and URL fields: `CONF_NAME`, `CONF_URL`
- refresh/timezone/prefix/time fields
- day count and lock-manager selector fields
- slot, max-events, code-length, code-generator, and update-code fields
- honor-event-times, ignore-non-reserved, SSL, cleaning-window, trim-name, and
  max-name-length fields
- options-only diagnostics and code-buffer fields

**Relationships**:

- Use existing constants from `const.py`.
- Use `SelectSelector`, `SelectSelectorConfig`, `SelectSelectorMode`, and
  `SelectOptionDict` exactly as current `_get_schema()` does.
- Use lock-manager config entries from `hass.config_entries.async_entries()`.

**Validation rules**:

- Required vs optional status remains unchanged.
- Defaults preserve current user-input-over-default precedence.
- Timezone choices remain `sorted(available_timezones())`.
- Code-generator display choices remain descriptions from `CODE_GENERATORS`.
- Lock selector always includes `(none)` first.

### URLValidationResult

**Owner module**: `config_flow_helpers.models` or `validation.py`

**Purpose**: Internal result for the submitted calendar URL check.

**Fields**:

- `error: str | None`
- `status: int | None`
- `reason: str | None`
- `content_type: str | None`

**Relationships**:

- Produced by URL validation helper after `cv.url()` and optional HTTP fetch.
- Consumed by flow validation to update `errors[CONF_URL]` and preserve logging.

**Validation rules**:

- Non-HTTP(S) and malformed URLs still map to `invalid_url`.
- HTTP URLs still map to `https_required` when SSL verification is enabled.
- Non-200 responses still log URL, status, and reason and map to `unknown`.
- Missing `text/calendar` in response content type still maps to `bad_ics`.

### FlowValidationResult

**Owner module**: `config_flow_helpers.models` or `validation.py`

**Purpose**: Accumulated submitted-data validation state.

**Fields**:

- `user_input: dict[str, Any]`
- `errors: dict[str, str]`
- `description_placeholders: dict[str, str]`
- `can_create_entry: bool`

**Relationships**:

- Starts with duplicate-detection errors from the shell when applicable.
- Receives URL, scalar, code-generator, trim-name, and successful-conversion
  results from validation helpers.
- Consumed by `steps.py` to choose `async_create_entry()` or form re-render.

**Validation rules**:

- Error keys and base errors match current `_start_config_flow()`.
- Code-generator descriptions convert to types before form re-render, matching
  current mutation timing.
- Lock-entry conversion to `None` or entity ID happens only when no errors exist.
- `CONF_CREATION_DATETIME` is inserted only for initial flows with `created`.
- `CONF_GENERATE` is inserted only when a successful lock entry is present.

### FlowTransitionRequest

**Owner module**: `config_flow_helpers.models` or `steps.py`

**Purpose**: Values needed to orchestrate one config/options flow step without a
long `_start_config_flow` body.

**Fields**:

- `flow: RentalControlFlowHandler | RentalControlOptionsFlow`
- `step_id: str`
- `title: str`
- `user_input: dict[str, Any] | None`
- `defaults: dict[str, Any] | None`
- `entry_id: str | None`

**Relationships**:

- Normalized from `_start_config_flow()` arguments.
- Uses the flow object's `hass`, optional `_get_unique_id()`,
  `async_show_form()`, and `async_create_entry()` methods.

**Validation rules**:

- No new flow step is introduced.
- `title` is passed unchanged to `async_create_entry()`.
- `defaults` and `entry_id` are passed unchanged to schema generation.
- The helper performs no work when showing the initial form beyond schema
  construction.
