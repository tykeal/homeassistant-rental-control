<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Quickstart: Explicit Entry Data Guards

**Feature Branch**: `010-explicit-entry-guards`
**Date**: 2026-06-18

## Overview

This refactor makes missing Rental Control domain data or entry data explicit at
six issue-reported access paths. Loaded entries should behave exactly as before.
Missing domain or entry data should short-circuit safely without raising and
without creating or mutating throwaway `{}` state.

## Implementation Scope

### Files Expected to Change

| File | Change |
|------|--------|
| `custom_components/rental_control/util.py` | Add `get_entry_data(hass, entry_id) -> dict[str, Any] | None` with a docstring and type hints |
| `custom_components/rental_control/__init__.py` | Use helper in the two `update_listener` lookups and the keymaster event forwarding lookup |
| `custom_components/rental_control/sensors/checkinsensor.py` | Use helper for monitoring-switch and early-expiry-switch entry-data lookups |
| `custom_components/rental_control/switch.py` | Use helper before storing the monitoring switch reference |
| `tests/unit/test_init.py` | Cover update-listener missing domain/entry behavior |
| `tests/unit/test_keymaster_event_diagnostics.py` | Cover keymaster event rejection when entry data is unavailable |
| `tests/unit/test_checkin_sensor.py` | Cover monitoring fallback and early-expiry skip behavior |
| `tests/unit/test_switch.py` | Cover no throwaway mutation when switch registration lacks entry data |

### Files Not Expected to Change

| Path | Why |
|------|-----|
| `specs/010-explicit-entry-guards/data-model.md` | No new data model is introduced |
| `specs/010-explicit-entry-guards/contracts/` | No API, service, entity, or configuration contract changes are introduced |
| Agent context files | No new technology, dependency, or platform context is introduced |

## Maintainer Verification

### 1. Inspect the Access Paths

Confirm the six issue-reported paths no longer use chained domain defaults,
including the multi-line lookups in `checkinsensor.py`:

```bash
rg --multiline 'data\.get\(DOMAIN, \{\}\)\s*\.get|data\.get\(DOMAIN, \{\}\)\.get' \
  custom_components/rental_control/__init__.py \
  custom_components/rental_control/sensors/checkinsensor.py \
  custom_components/rental_control/switch.py
```

Expected result: no matches for the six issue-reported paths. If this command
still reports a nearby non-reported diagnostic lookup, confirm whether it is
directly coupled to the event-path implementation before broadening scope.

Confirm the shared helper exists and is imported by each changed module:

```bash
rg 'get_entry_data' custom_components/rental_control tests
```

### 2. Verify Loaded-Entry Behavior

Run targeted tests that exercise normal setup, listener refresh, event
diagnostics, check-in monitoring, checkout, and switch registration:

```bash
uv run pytest \
  tests/unit/test_init.py \
  tests/unit/test_keymaster_event_diagnostics.py \
  tests/unit/test_checkin_sensor.py \
  tests/unit/test_switch.py \
  tests/integration/test_full_setup.py
```

Expected result: all selected tests pass, with no behavior regression for a
normally loaded entry where `hass.data[DOMAIN][entry_id]` exists.

### 3. Verify Missing-Data Safety

Add or review tests that remove `hass.data[DOMAIN]` and, separately, remove
`hass.data[DOMAIN][entry_id]` before invoking each affected operation. Confirm:

- `update_listener()` returns without mutating config-entry data when entry data
  is unavailable before the update.
- Listener refresh returns safely if entry data disappears after
  `coordinator.update_config()`.
- Keymaster unlock handling returns without forwarding or recording an accepted
  event when entry data is unavailable.
- `_is_keymaster_monitoring_enabled()` uses the existing configured-lock
  fallback when entry data or the monitoring switch is unavailable.
- Manual checkout skips early expiry when entry data or the early-expiry switch
  is unavailable, then continues the checkout transition.
- `KeymasterMonitoringSwitch.async_added_to_hass()` does not create or mutate a
  throwaway dict when entry data is unavailable.

### 4. Run Linting

```bash
uv run ruff check \
  custom_components/rental_control/util.py \
  custom_components/rental_control/__init__.py \
  custom_components/rental_control/sensors/checkinsensor.py \
  custom_components/rental_control/switch.py \
  tests/unit/test_init.py \
  tests/unit/test_keymaster_event_diagnostics.py \
  tests/unit/test_checkin_sensor.py \
  tests/unit/test_switch.py
```

Expected result: ruff passes. Pre-commit hooks and CI must also pass before the
implementation PR is merged.
