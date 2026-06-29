<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Quickstart: Config Flow Decomposition Parity

This quickstart is for the later IMPLEMENT stage. The PLAN stage is docs-only
and does not modify production code.

## Scope

This feature is a behavior-preserving refactor only. Do not add configuration
options, flow steps, validation rules, selectors, defaults, services, storage,
calendar fetches, Home Assistant state writes, or public caller behavior changes.

## 1. Establish the existing behavior oracle

Run the current config-flow tests before extraction:

```bash
uv run pytest tests/unit/test_config_flow.py -q
```

Confirm visible test seams that must remain:

```bash
rg "from custom_components\.rental_control\.config_flow" tests/unit/test_config_flow.py
rg "custom_components\.rental_control\.config_flow\.gen_uuid" tests/unit/test_config_flow.py
```

Confirm source facts before editing:

```bash
wc -l custom_components/rental_control/config_flow.py
rg "def _get_schema|def _show_config_form|async def _start_config_flow|aislop-ignore" \
  custom_components/rental_control/config_flow.py -n
```

Expected planning facts are a 549-line file, long `_get_schema` and
`_start_config_flow`, seven-parameter `_show_config_form`, and only the existing
`ai-slop/hallucinated-import` directive.

## 2. Add helper modules and keep wrappers first

Add project SPDX headers, type hints, and docstrings to any new Python files:

- `custom_components/rental_control/config_flow_helpers/__init__.py`
- `custom_components/rental_control/config_flow_helpers/models.py`
- `custom_components/rental_control/config_flow_helpers/schemas.py`
- `custom_components/rental_control/config_flow_helpers/validation.py`
- `custom_components/rental_control/config_flow_helpers/steps.py`

Keep `RentalControlFlowHandler` and `RentalControlOptionsFlow` in
`config_flow.py`. Move behavior one concern at a time, leaving compatibility
wrappers in `config_flow.py` until all parity tests pass.

## 3. Preserve the Home Assistant shell

After shell wiring, run an import and attribute smoke check:

```bash
uv run python - <<'PY'
from custom_components.rental_control import config_flow

assert config_flow.RentalControlFlowHandler.VERSION == 10
assert hasattr(config_flow.RentalControlFlowHandler, "async_step_user")
assert hasattr(config_flow.RentalControlFlowHandler, "async_get_options_flow")
assert hasattr(config_flow.RentalControlOptionsFlow, "async_step_init")
for name in [
    "gen_uuid",
    "_normalize_lock_entry",
    "_get_schema",
    "_show_config_form",
    "_start_config_flow",
]:
    assert hasattr(config_flow, name), name
PY
```

`RentalControlFlowHandler._get_unique_id()` must continue to call
`config_flow.gen_uuid` at runtime so this existing patch path remains effective:

```text
custom_components.rental_control.config_flow.gen_uuid
```

## 4. Extract schema construction

Move `_get_schema` behavior into focused builders while keeping
`config_flow._get_schema()` importable. Pin parity for:

- required `CONF_NAME`, `CONF_URL`, `CONF_CHECKIN`, `CONF_CHECKOUT`,
  `CONF_START_SLOT`, `CONF_MAX_EVENTS`, and `CONF_CODE_LENGTH` fields;
- optional refresh, timezone, prefix, days, lock-entry, code-generation,
  update-code, honor-event-times, ignore-non-reserved, SSL, cleaning-window,
  trim-name, and max-name-length fields;
- options-only diagnostics and code-buffer fields when `entry_id` is provided;
- `ALLOW_EXTRA` behavior;
- `(none)` lock selector first, followed by lock-manager titles;
- stored lock entity IDs displayed as lock-manager titles in defaults;
- code-generator descriptions shown in the form.

Suggested targeted command after adding schema parity tests:

```bash
uv run pytest \
  tests/unit/test_config_flow.py \
  tests/unit/test_config_flow_schemas.py \
  -q
```

## 5. Extract validation and step transitions

Move `_start_config_flow` validation into focused helpers while keeping
`config_flow._start_config_flow()` importable. Preserve validation order and
mutation timing for:

- lock-entry normalization before schema/form rendering;
- duplicate detection through the shell's `_get_unique_id()`;
- URL syntax, HTTPS-required, fetch timeout, non-200, and content-type checks;
- refresh-frequency, check-in, checkout, days, max-events, code-length, and
  max-name-length errors;
- trim-name prefix boundary base error;
- code-generator description-to-type conversion before re-rendering on later
  errors;
- successful `(none)` to `None` and lock-manager title to entity conversion;
- `CONF_CREATION_DATETIME` insertion for initial setup;
- `CONF_GENERATE` insertion when a lock entry is selected;
- unchanged `async_create_entry()` title and data.

Suggested targeted command after adding validation parity tests:

```bash
uv run pytest \
  tests/unit/test_config_flow.py \
  tests/unit/test_config_flow_validation.py \
  -q
```

## 6. Reduce `_show_config_form` parameters

Introduce `ConfigFormContext` and update both internal call sites in
`_start_config_flow`:

```python
_show_config_form(
    flow,
    ConfigFormContext(
        step_id=step_id,
        user_input=user_input,
        errors=errors,
        description_placeholders=description_placeholders,
        defaults=defaults,
        entry_id=entry_id,
    ),
)
```

The error re-render path around current line 531 and the initial-form path around
current line 541 must both use the context. `_show_config_form` remains importable
from `config_flow.py`, returns the same `async_show_form()` result, and declares
no more than six parameters.

## 7. Validate behavior parity

Run the smallest behavior oracle first:

```bash
uv run pytest tests/unit/test_config_flow.py -q
```

Then run focused helper tests added during implementation:

```bash
uv run pytest \
  tests/unit/test_config_flow_schemas.py \
  tests/unit/test_config_flow_validation.py \
  -q
```

Run broader caller smoke coverage that exercises setup-created entries and
refresh duplicate assumptions:

```bash
uv run pytest \
  tests/integration/test_full_setup.py \
  tests/integration/test_refresh_cycle.py \
  -q
```

Before committing implementation changes, run the full existing suite and ruff:

```bash
uv run pytest tests/
uv run ruff check custom_components/ tests/
```

## 8. Measure complexity before claiming completion

Confirm config-flow files stay below the active thresholds:

```bash
wc -l \
  custom_components/rental_control/config_flow.py \
  custom_components/rental_control/config_flow_helpers/*.py
```

Measure function lengths and parameter counts with the repository's existing
complexity tooling or an AST check, and confirm:

- no config-flow-related file is 400 lines or longer;
- no project-owned function is 80 lines or longer;
- no project-owned parameter list has more than six parameters;
- the existing `ai-slop/hallucinated-import` directive remains in
  `config_flow.py`;
- no `aislop-ignore`, `aislop-ignore-file`, or equivalent complexity suppression
  was added.

## Behavior parity reminders

- Flow classes, `VERSION`, `async_step_user`, `async_step_init`,
  `async_get_options_flow`, and domain registration remain in `config_flow.py`.
- Initial and options step IDs remain `user` and `init`.
- Schema keys, required/optional status, defaults, selectors, and `ALLOW_EXTRA`
  remain unchanged.
- URL validation and fetch behavior remain unchanged and happen only for submitted
  data.
- Error keys and base errors remain unchanged.
- Created entry titles and data remain unchanged.
- Options flow loads existing `config_entry.data` exactly as today.
- `_normalize_lock_entry`, `_get_schema`, `_show_config_form`, `_start_config_flow`,
  and `gen_uuid` remain importable from `config_flow.py`.
