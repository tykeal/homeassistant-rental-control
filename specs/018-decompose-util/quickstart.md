<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Quickstart: Decompose Util

## Scope

This feature is a behavior-preserving refactor only. Do not add lock-code
business rules, sensors, services, configuration options, reconciliation
launches, Store authority, or user-visible behavior changes.

## Implementation checklist

1. Start from `origin/main` and keep `util.py` as the public module.
2. Add focused sibling modules:
   - `custom_components/rental_control/keymaster_services.py`
   - `custom_components/rental_control/state_handlers.py`
   - `custom_components/rental_control/helpers.py`
3. Move behavior behind wrappers one concern at a time:
   - first add compatibility smoke tests around current behavior;
   - extract generic helpers and keep all util imports passing;
   - extract Keymaster service helpers with util-supplied dependencies;
   - extract state-change handling with util-supplied sleep;
   - adjust `event_overrides.py` and `coordinator.py` wrappers so they call
     `util.<name>` at runtime while keeping their visible patch names.
4. Keep all new Python files licensed with project SPDX headers and all public
   functions/classes documented.
5. Do not add an `aislop-ignore-file` directive for utility complexity.

## Import and patch smoke checks

Run these after the wrappers are in place:

```bash
uv run python - <<'PY'
from custom_components.rental_control import util

required = [
    "dt",
    "is_cleared_keymaster_text_state",
    "is_unreadable_keymaster_text_state",
    "normalize_keymaster_text_state",
    "OperationResult",
    "get_entry_data",
    "normalize_uid",
    "check_gather_results",
    "add_call",
    "delete_rc_and_base_folder",
    "delete_folder",
    "async_fire_clear_code",
    "trim_name",
    "apply_buffer",
    "async_fire_set_code",
    "async_fire_update_times",
    "EventIdentity",
    "get_event_identities",
    "get_event_names",
    "gen_uuid",
    "compute_early_expiry_time",
    "get_slot_name",
    "handle_state_change",
    "async_reload_package_platforms",
]
missing = [name for name in required if not hasattr(util, name)]
assert not missing, missing
assert hasattr(util, "asyncio")
assert hasattr(util, "pn_create")
assert hasattr(util, "pn_dismiss")
assert hasattr(util, "async_track_state_change_event")
assert hasattr(util, "_SET_CODE_CONFIRMATION_TIMEOUT")
PY
```

Add unit tests that patch these paths and verify the patched object is observed:

- `custom_components.rental_control.util.async_fire_set_code`
- `custom_components.rental_control.util.async_fire_clear_code`
- `custom_components.rental_control.util.async_fire_update_times`
- `custom_components.rental_control.util.get_event_identities`
- `custom_components.rental_control.event_overrides.async_fire_set_code`
- `custom_components.rental_control.event_overrides.async_fire_clear_code`
- `custom_components.rental_control.event_overrides.async_fire_update_times`
- `custom_components.rental_control.event_overrides.get_event_identities`
- `custom_components.rental_control.coordinator.async_fire_clear_code`
- `custom_components.rental_control.coordinator.add_call`
- `custom_components.rental_control.util.asyncio.sleep`
- `custom_components.rental_control.util.pn_create`
- `custom_components.rental_control.util.pn_dismiss`
- `custom_components.rental_control.util.async_track_state_change_event`
- `custom_components.rental_control.util._SET_CODE_CONFIRMATION_TIMEOUT`

## Targeted tests

Run the smallest parity set first:

```bash
uv run pytest \
  tests/unit/test_util.py \
  tests/unit/test_event_overrides.py \
  tests/unit/test_event_overrides_apply.py \
  tests/unit/test_coordinator.py \
  tests/unit/test_coordinator_buffer_update.py \
  tests/integration/test_refresh_cycle.py \
  tests/integration/test_slot_concurrency.py \
  -x -q
```

Then run the full project suite before committing implementation changes:

```bash
uv run pytest tests/
uv run ruff check custom_components/ tests/
```

## Complexity measurements

Before claiming implementation complete, measure line counts:

```bash
wc -l \
  custom_components/rental_control/util.py \
  custom_components/rental_control/keymaster_services.py \
  custom_components/rental_control/state_handlers.py \
  custom_components/rental_control/helpers.py
```

Confirm every listed file is below 400 lines. If `helpers.py` nears the limit,
split by stable sub-concern instead of adding a suppression.

Measure function lengths and parameter counts with the repository's existing
complexity tooling or an AST check, and confirm:

- no project-owned function is 80 lines or longer;
- no project-owned parameter list has more than six parameters;
- no new `aislop-ignore-file` directive suppresses utility file-size or
  function-length findings.

## Behavior parity reminders

- Set-code order remains disable, date-range enable, end/start/PIN/name writes,
  then slot enable.
- Clear-code still performs ownership verification, reset button press,
  propagation wait, unreadable/missing checks, forced name clear, lingering-name
  and lingering-PIN classification, retry state, and notification handling.
- Update-times still writes end before start and confirms both datetimes.
- State-change callbacks still settle, handle reset entities, suppress feedback,
  gate on enabled/date-range state, preserve existing override values during
  feedback, reject code-without-name, restore trimmed names only on exact trimmed
  matches, and call `update_event_overrides` without launching reconciliation.
- `is_cleared_keymaster_text_state`, `is_unreadable_keymaster_text_state`,
  `normalize_keymaster_text_state`, and `apply_buffer` are load-bearing for
  self-heal and must remain byte-for-byte equivalent in observed behavior.
