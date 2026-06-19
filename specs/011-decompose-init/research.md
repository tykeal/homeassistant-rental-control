<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Research: Decompose Integration Entry Module

**Feature Branch**: `011-decompose-init`
**Date**: 2026-06-19

## Research Task 1: Target Module Names

**Question**: Should the implementation use `migrations.py` plus `listeners.py`,
or a more specific listener module such as `keymaster_listener.py`?

### Decision

Use `custom_components/rental_control/migrations.py` and
`custom_components/rental_control/listeners.py`.

### Rationale

`migrations.py` exactly matches the moved Home Assistant migration entry point.
`listeners.py` is the best listener target because the moved code is listener
registration and event filtering, while the package still owns another listener
orchestration function, `async_start_listener()`. The plural name leaves room for
future listener helpers without making this refactor broader than Keymaster
event-bus handling.

### Alternatives considered

- **`keymaster_listener.py`**: More explicit for the current code, but less
  consistent with the generic integration-level listener responsibility and the
  spec default.
- **Keep listener code in `__init__.py`**: Rejected because it leaves the largest
  issue-reported function and its nested event handler in the entry module.

## Research Task 2: Live Function and Symbol Inventory

**Question**: Which symbols do the moved functions reference, and what must each
new module import without creating circular imports?

### Decision

Move only the issue-reported bodies into new modules and import their direct
dependencies from Home Assistant, `const.py`, and `util.py`. Do not import from
`custom_components.rental_control.__init__` in either new module.

### Rationale

The live source in `custom_components/rental_control/__init__.py` has these
relevant top-level functions:

| Function | Live lines | Length | Plan |
|----------|------------|--------|------|
| `async_setup_entry` | 70-124 | 55 | Stay in `__init__.py` |
| `async_unload_entry` | 127-166 | 40 | Stay in `__init__.py` |
| `async_migrate_entry` | 169-300 | 132 | Move to `migrations.py` and helper-split |
| `update_listener` | 303-346 | 44 | Stay in `__init__.py` |
| `async_start_listener` | 349-376 | 28 | Stay in `__init__.py` |
| `@callback async_register_keymaster_listener` | 379-543 | 165 including decorator; 164-line body | Move to `listeners.py` and helper-split |
| `_handle_keymaster_event` | 403-534 | 132 | Extract within `listeners.py` |

#### `migrations.py` import inventory

`async_migrate_entry()` references these imported symbols today:

| Symbol | Source | Use |
|--------|--------|-----|
| `ConfigEntry` | `homeassistant.config_entries` | type hint |
| `HomeAssistant` | `homeassistant.core` | type hint and `hass.config_entries.async_update_entry(...)` |
| `CONF_CODE_LENGTH` | `.const` | v3→v4 migration guard/default |
| `CONF_GENERATE` | `.const` | v4→v5 migration data write |
| `CONF_PATH` | `.const` | v5→v6 removal |
| `CONF_SHOULD_UPDATE_CODE` | `.const` | v6→v7 default |
| `CONF_HONOR_EVENT_TIMES` | `.const` | v7→v8 default |
| `CONF_TRIM_NAMES` | `.const` | v8→v9 default |
| `CONF_MAX_NAME_LENGTH` | `.const` | v8→v9 default |
| `CONF_CODE_BUFFER_BEFORE` | `.const` | v9→v10 default |
| `CONF_CODE_BUFFER_AFTER` | `.const` | v9→v10 default |
| `DEFAULT_CODE_LENGTH` | `.const` | v3→v4 default |
| `DEFAULT_GENERATE` | `.const` | v4→v5 default |
| `DEFAULT_MAX_NAME_LENGTH` | `.const` | v8→v9 default |
| `DEFAULT_CODE_BUFFER_BEFORE` | `.const` | v9→v10 default |
| `DEFAULT_CODE_BUFFER_AFTER` | `.const` | v9→v10 default |
| `_LOGGER` | module local | migration debug/error logging |

The module should import `logging` and define `_LOGGER` locally. Using
`logging.getLogger(__name__)` follows the repository logger convention without
importing `_LOGGER` from `__init__.py`.

#### `listeners.py` import inventory

`async_register_keymaster_listener()` and its inner closures reference these
imported symbols today:

| Symbol | Source | Use |
|--------|--------|-----|
| `ConfigEntry` | `homeassistant.config_entries` | type hint |
| `Event` | `homeassistant.core` | event handler type hint |
| `HomeAssistant` | `homeassistant.core` | type hint and HA data/bus access |
| `callback` | `homeassistant.core` | callback decorators |
| `dt_util` | `homeassistant.util.dt` | diagnostic timestamps |
| `slugify` | `homeassistant.util` | event lock-name normalization |
| `CHECKIN_SENSOR` | `.const` | check-in sensor lookup and diagnostic refresh |
| `CONF_ENABLE_KEYMASTER_EVENT_DIAGNOSTICS` | `.const` | diagnostics option lookup |
| `COORDINATOR` | `.const` | coordinator lookup from `hass.data` |
| `DEFAULT_ENABLE_KEYMASTER_EVENT_DIAGNOSTICS` | `.const` | diagnostics option default |
| `DOMAIN` | `.const` | `hass.data` domain lookup |
| `KEYMASTER_MONITORING_SWITCH` | `.const` | monitoring switch lookup |
| `UNSUB_LISTENERS` | `.const` | unsubscribe callback storage |
| `get_entry_data` | `.util` | safe entry-data lookup before forwarding |
| `_LOGGER` | module local | debug logging |

The moved listener code also uses coordinator attributes
`start_slot`, `max_events`, `monitored_locknames`, and
`keymaster_event_diagnostics`, mutates `hass.data[DOMAIN][entry_id][UNSUB_LISTENERS]`
by appending the bus unsubscribe callback, reads `config_entry.data`, and calls
`checkin_sensor.async_handle_keymaster_unlock(...)` and
`sensor.async_write_ha_state()` when available.

#### Remaining `__init__.py` imports

After moving the two bodies, `__init__.py` should keep imports needed by setup,
unload, update, and `async_start_listener()`:

- standard library: `functools`, `logging`;
- Home Assistant domains: `BUTTON`, `DATETIME`, `SWITCH`, `TEXT`;
- Home Assistant helpers: `async_create`, `async_dismiss`, `ConfigEntry`,
  `CONF_NAME`, `HomeAssistant`, `ConfigEntryNotReady`,
  `async_track_state_change_event`;
- constants: `CONF_CREATION_DATETIME`, `CONF_GENERATE`, `COORDINATOR`, `DOMAIN`,
  `NAME`, `PLATFORMS`, `UNSUB_LISTENERS`;
- coordinator/helper symbols: `RentalControlCoordinator`,
  `async_reload_package_platforms`, `delete_rc_and_base_folder`,
  `get_entry_data`, and `handle_state_change`;
- re-export imports from the new modules:
  `async_migrate_entry` and `async_register_keymaster_listener`.

Imports used only by moved code should leave `__init__.py`: `Event`, `callback`,
`dt_util`, `slugify`, `CHECKIN_SENSOR`, `CONF_CODE_BUFFER_AFTER`,
`CONF_CODE_BUFFER_BEFORE`, `CONF_CODE_LENGTH`,
`CONF_ENABLE_KEYMASTER_EVENT_DIAGNOSTICS`, `CONF_HONOR_EVENT_TIMES`,
`CONF_MAX_NAME_LENGTH`, `CONF_PATH`, `CONF_SHOULD_UPDATE_CODE`,
`CONF_TRIM_NAMES`, `DEFAULT_CODE_BUFFER_AFTER`, `DEFAULT_CODE_BUFFER_BEFORE`,
`DEFAULT_CODE_LENGTH`, `DEFAULT_ENABLE_KEYMASTER_EVENT_DIAGNOSTICS`,
`DEFAULT_GENERATE`, `DEFAULT_MAX_NAME_LENGTH`, and
`KEYMASTER_MONITORING_SWITCH`.

### Alternatives considered

- **Import `_LOGGER` or helpers from `__init__.py` into new modules**: Rejected
  because it creates a package import cycle once `__init__.py` imports the moved
  public functions from the new modules.
- **Move shared constants into a third new module**: Rejected as unnecessary; all
  required values already live in `const.py`.
- **Leave moved modules with wildcard or package imports**: Rejected because the
  public-import contract must be explicit and circular-import risk must remain
  visible.

## Research Task 3: Caller Relationships and `async_start_listener()`

**Question**: Who calls the moved functions, and should `async_start_listener()`
move with the listener code?

### Decision

Keep `async_start_listener()` in `__init__.py`. Move only
`async_register_keymaster_listener()` and its event-handling helpers to
`listeners.py`.

### Rationale

The live call graph is:

- Home Assistant imports the integration package and calls package-level
  `async_setup_entry()`, `async_unload_entry()`, and `async_migrate_entry()`.
- `async_setup_entry()` calls `async_start_listener()` before platform setup and
  calls `async_register_keymaster_listener()` after platform setup when
  `coordinator.lockname` is set.
- `update_listener()` calls `async_start_listener()` and
  `async_register_keymaster_listener()` after replacing existing unsubscribe
  callbacks when a configured lock still exists.
- Tests patch `custom_components.rental_control.async_start_listener` and
  `custom_components.rental_control.async_register_keymaster_listener` in
  `tests/unit/test_init.py`.
- Keymaster event tests import
  `async_register_keymaster_listener` from `custom_components.rental_control`.

`async_start_listener()` is only 28 lines and tracks Keymaster state-change
entities through `async_track_state_change_event(...)` and
`handle_state_change(...)`. Keeping it in `__init__.py` preserves setup/update
orchestration and avoids moving unrelated state-change-listener responsibilities.
Removing the 132-line migration body and the 164-line event-listener body leaves
`__init__.py` safely below the 400-line threshold even when
`async_start_listener()` stays.

### Alternatives considered

- **Move `async_start_listener()` to `listeners.py`**: Rejected because it is not
  over threshold, is directly tied to setup/update orchestration, and would force
  more package-level patch/re-export considerations for no threshold benefit.
- **Move all listener-related code out of `__init__.py`**: Rejected as broader
  than issue #572 and the spec's requirement to keep orchestration in the entry
  module.

## Research Task 4: Public Package Re-Exports

**Question**: Which names must remain importable from
`custom_components.rental_control` after moving code?

### Decision

`__init__.py` must continue to expose these package-level callables:

1. `async_setup_entry` — stays defined in `__init__.py` for Home Assistant;
2. `async_unload_entry` — stays defined in `__init__.py` for Home Assistant;
3. `async_migrate_entry` — re-export from `.migrations` for Home Assistant and
   migration tests;
4. `update_listener` — stays defined in `__init__.py` for tests and config-entry
   update callbacks;
5. `async_start_listener` — stays defined in `__init__.py` for tests that patch
   the package-level name; and
6. `async_register_keymaster_listener` — re-export from `.listeners` for setup,
   update, and tests.

Implementation should import the moved functions near the other local imports:

```python
from .listeners import async_register_keymaster_listener
from .migrations import async_migrate_entry
```

### Rationale

Live test-suite imports and patches from the package level are:

| File | Package-level name |
|------|--------------------|
| `tests/unit/test_init.py:18` | `update_listener` |
| `tests/unit/test_init.py:221,326` | patched `async_start_listener` |
| `tests/unit/test_init.py:225,330` | patched `async_register_keymaster_listener` |
| `tests/unit/test_init.py:424,463,505,547,595` | `async_migrate_entry` |
| `tests/unit/test_keymaster_event_diagnostics.py:20` | `async_register_keymaster_listener` |
| `tests/unit/test_checkin_sensor.py:2554-3507` | `async_register_keymaster_listener` |

Home Assistant also resolves `async_migrate_entry` at the integration package
level. Re-exporting the moved function preserves
`custom_components.rental_control.async_migrate_entry` exactly where HA and tests
expect it.

### Alternatives considered

- **Update tests to import new modules directly**: Rejected as the primary
  compatibility risk. Tests should continue proving the package-level public
  contract remains stable.
- **Only expose moved functions through new modules**: Rejected because it would
  break Home Assistant migration discovery and existing tests/importers.

## Research Task 5: Helper Split for Function-Length Thresholds

**Question**: Is moving the long functions sufficient, or must the implementation
split them into smaller helpers?

### Decision

Split both moved responsibilities into private helpers in their new modules.

For `migrations.py`, keep package-level `async_migrate_entry()` as the Home
Assistant entry point, but make it a short orchestration function that calls
private per-version helpers:

- `_migrate_v3_to_v4(hass, config_entry) -> None`
- `_migrate_v4_to_v5(hass, config_entry) -> None`
- `_migrate_v5_to_v6(hass, config_entry) -> None`
- `_migrate_v6_to_v7(hass, config_entry) -> None`
- `_migrate_v7_to_v8(hass, config_entry) -> None`
- `_migrate_v8_to_v9(hass, config_entry) -> None`
- `_migrate_v9_to_v10(hass, config_entry) -> None`

For `listeners.py`, keep package-level `async_register_keymaster_listener()` as
the public registration callback, but move event processing into private helpers
such as:

- `_handle_keymaster_event(...) -> None`
- `_record_keymaster_event_disposition(...) -> None`
- `_refresh_checkin_sensor_state(...) -> None`
- `_forward_keymaster_unlock(...) -> bool`

Exact helper names may be adjusted during implementation, but each helper must
remain under 80 lines and preserve the existing ordering of validation,
diagnostics, logging, and forwarding.

### Rationale

The live `async_migrate_entry()` body is 132 lines. Moving it unchanged would
reduce `__init__.py` size but would still leave an in-scope function over the
80-line threshold in `migrations.py`. Per-version helpers are the minimal split
that mirrors the existing ordered comments and keeps every version transition
reviewable.

The live `async_register_keymaster_listener()` span is 165 lines including the
`@callback` decorator, its function body is 164 lines, and its nested
`_handle_keymaster_event()` closure is 132 lines. Moving both unchanged would
preserve behavior but fail the function-length objective. Extracting diagnostics
and forwarding helpers keeps the public registration function short while
preserving the current event-filter order:

1. slugify the raw lock name;
2. return immediately for unmonitored locks without recording diagnostics;
3. read `code_slot_num` and the diagnostics option;
4. reject non-`"unlocked"` states, missing/zero slots, and out-of-range slots
   with the same diagnostic dispositions;
5. look up entry data with `get_entry_data(...)`;
6. reject missing check-in sensor, missing/off monitoring switch, and record the
   same diagnostic dispositions; and
7. record `accepted` and call `checkin_sensor.async_handle_keymaster_unlock(...)`
   with the same `code_slot_num` and raw lock name.

### Alternatives considered

- **Move the functions unchanged**: Rejected because it does not satisfy the
  80-line threshold for the in-scope functions after decomposition.
- **Introduce a migration class or listener class**: Rejected as unnecessary for
  a no-behavior-change refactor and more complex than private helper functions.
- **Change diagnostics semantics while splitting**: Rejected as out of scope;
  downstream diagnostics dispositions and buffer behavior must remain unchanged.

## Summary

The implementation stage should add `migrations.py` and `listeners.py`, move and
helper-split only the two issue-reported responsibilities, re-export the moved
public entry points from `__init__.py`, keep `async_start_listener()` in
`__init__.py`, and avoid circular imports by importing only from Home Assistant,
`const.py`, and `util.py` in the new modules.
