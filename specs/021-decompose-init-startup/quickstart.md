<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Quickstart: Init Startup Readability Decomposition Parity

This quickstart is for the later IMPLEMENT stage. The PLAN stage is docs-only
and does not modify production code.

## Scope

This feature is a behavior-preserving refactor only. Do not add configuration
options, services, entities, diagnostics fields, storage, listener semantics,
startup refresh semantics, lock-code business rules, Home Assistant state writes,
Keymaster service calls, or changed public caller behavior.

## 1. Establish the existing behavior oracle

Run the current entry tests before extraction:

```bash
uv run pytest tests/unit/test_init.py -q
```

Confirm visible import and patch seams that must remain:

```bash
rg "async_arm_startup_readability_refresh|async_start_listener" \
  tests/unit/test_init.py -n
```

Confirm source facts before editing:

```bash
wc -l custom_components/rental_control/__init__.py
rg "def _managed_slot_readability_entity_ids|def _is_readable_keymaster_state|def _all_managed_slots_readable|def _needs_startup_readability_refresh|def async_arm_startup_readability_refresh|def async_start_listener|aislop-ignore" \
  custom_components/rental_control/__init__.py -n
```

Expected planning facts are an over-threshold `__init__.py`, a 143-line
`async_arm_startup_readability_refresh`, four startup-readability helpers, and
no Aislop directive in the file.

## 2. Add the startup-readability module

Add `custom_components/rental_control/startup_readability.py` with the project
SPDX header, type hints, and docstrings. Move the startup-readability constants,
entity discovery, readable-state checks, current needs helper, public arming
function, and watcher lifecycle into this module.

Keep imports one-way:

- `startup_readability.py` may import constants, coordinator types, and
  `get_entry_data` from sibling modules.
- `startup_readability.py` must not import from `custom_components.rental_control`
  package `__init__.py`, avoiding circular imports.
- `__init__.py` imports the public arming function and private needs helper from
  `startup_readability.py`.

## 3. Preserve package-level compatibility

After wiring, run an import smoke check:

```bash
uv run python - <<'PY'
import custom_components.rental_control as rc

for name in [
    "async_setup_entry",
    "async_unload_entry",
    "update_listener",
    "async_start_listener",
    "async_migrate_entry",
    "async_register_keymaster_listener",
    "async_arm_startup_readability_refresh",
]:
    assert hasattr(rc, name), name

assert rc.async_arm_startup_readability_refresh.__name__ == (
    "async_arm_startup_readability_refresh"
)
PY
```

`async_arm_startup_readability_refresh` must remain callable from:

```text
custom_components.rental_control
```

`update_listener` must continue to resolve the package-level
`async_start_listener` name so this current test patch remains effective:

```text
custom_components.rental_control.async_start_listener
```

## 4. Introduce the watcher object

Replace the nested callback state in `async_arm_startup_readability_refresh` with
a private watcher class or slots dataclass. Map the current closures to methods:

| Current closure | Watcher method |
|-----------------|----------------|
| `_remove_listener_reference` | `remove_listener_reference()` |
| `_cancel_watchers` | `cancel_watchers()` |
| `_remove_self` | `remove_self()` |
| `_refresh_done` | `refresh_done(task)` |
| `_async_refresh_once` | `async_refresh_once()` |
| `_refresh_if_readable` | `refresh_if_readable(now)` |
| `_schedule_refresh` | `schedule_refresh(event)` |
| `_expire` | `expire(now)` |

Keep the public arming function short: compute need, return if no need,
construct the watcher, and call `arm()`.

## 5. Preserve readiness and missed-transition behavior

Pin these cases with existing tests and any focused helper tests added during
implementation:

- no lock name returns no watched entities and no watcher;
- each managed slot watches name, pin, and enabled entities only;
- `None` and `STATE_UNAVAILABLE` are unreadable;
- `STATE_UNKNOWN` and normal switch/text states are readable;
- readable startup slots do not add the startup watcher;
- `startup_slots_unreadable=True` schedules the delayed one-shot refresh even
  when entities are already readable by arm time;
- readable-to-readable state changes do not reschedule the debounce timer;
- rapid unreadable-to-readable transitions collapse into one refresh.

Suggested targeted command:

```bash
uv run pytest \
  tests/unit/test_init.py::test_healthy_startup_does_not_arm_watcher \
  tests/unit/test_init.py::test_startup_readability_watcher_handles_missed_transition \
  -q
```

## 6. Preserve cleanup, watchdog, and refresh semantics

Verify the watcher keeps the current lifecycle details:

- state subscription, watchdog, and cleanup reference are created during `arm()`;
- debounce timer is cancelled before replacement;
- `cancel_watchers()` cancels debounce, watchdog, and state subscription in the
  current order;
- unload cleanup marks `done`, cancels timers/listeners, cancels a pending
  refresh task, and removes the cleanup callback;
- watchdog expiry logs the same debug message and removes the watcher;
- missing entry data before refresh skips safely;
- coordinator refresh exceptions are logged and do not propagate;
- refresh completion removes the cleanup reference from `UNSUB_LISTENERS`.

Suggested targeted command:

```bash
uv run pytest \
  tests/unit/test_init.py::test_startup_readability_watcher_unloads_cleanly \
  tests/unit/test_init.py -q
```

## 7. Validate setup, unload, and update-listener parity

Run the full entry test oracle:

```bash
uv run pytest tests/unit/test_init.py -q
```

Run broader setup and startup-refresh smoke coverage:

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

Confirm the entry and startup-readability files stay below active thresholds:

```bash
wc -l \
  custom_components/rental_control/__init__.py \
  custom_components/rental_control/startup_readability.py
```

Measure function lengths and parameter counts with the repository's existing
complexity tooling or an AST check, and confirm:

- `__init__.py` is below 400 lines;
- `startup_readability.py` is below 400 lines;
- `async_arm_startup_readability_refresh` is below 80 lines;
- every watcher/helper function is below 80 lines;
- no project-owned parameter list has more than six parameters;
- no `aislop-ignore`, `aislop-ignore-file`, or equivalent suppression was added.

## Behavior parity reminders

- The PLAN and implementation are grounded in current `origin/main`
  `__init__.py`; issue shorthand is secondary when source differs.
- Do not rewrite `tests/unit/test_init.py` behavior assertions to fit the
  refactor. Existing tests should pass unchanged.
- Preserve the one-shot guarantee: once all watched entities settle, exactly one
  delayed coordinator refresh is scheduled by the startup watcher.
- Preserve the package-level `async_start_listener` patch seam. Do not route
  `update_listener` through a helper alias that hidden or visible patches cannot
  intercept.
