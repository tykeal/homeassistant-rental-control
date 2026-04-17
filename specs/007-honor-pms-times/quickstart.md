<!--
SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Quickstart: Honor PMS Calendar Event Times

**Feature**: 007-honor-pms-times
**Date**: 2025-07-22

## Prerequisites

- Python ≥ 3.14.2
- [uv](https://docs.astral.sh/uv/) package manager (see `UV_USAGE.md`)
- Home Assistant development environment (pytest-homeassistant-custom-component)
- Pre-commit hooks installed (`pre-commit install`)

## Setup

```bash
# From repository root
cd /home/tykeal/repos/personal/homeassistant/worktrees/007-honor-pms-times

# Install dependencies
uv sync

# Verify pre-commit hooks are installed
pre-commit run --all-files
```

## Implementation Order (Atomic Commits)

Each step produces one atomic commit:

### 1. Add constants (`const.py`)

Add to `const.py`:
```python
CONF_HONOR_EVENT_TIMES = "honor_event_times"
DEFAULT_HONOR_EVENT_TIMES = False
```

Place after the existing `CONF_SHOULD_UPDATE_CODE` / `DEFAULT_SHOULD_UPDATE_CODE` entries.

### 2. Add config flow toggle (`config_flow.py`)

- Import `CONF_HONOR_EVENT_TIMES` and `DEFAULT_HONOR_EVENT_TIMES`
- Add to `DEFAULTS` dict in `RentalControlFlowHandler`
- Add `vol.Optional` entry in `_get_schema()` after `CONF_SHOULD_UPDATE_CODE`
- Bump `VERSION` from 7 to 8

### 3. Add migration (`__init__.py`)

Add v7 → v8 migration block after the existing v6 → v7 block:
```python
if version == 7:
    _LOGGER.debug("Migrating from version %s", version)
    data = config_entry.data.copy()
    data[CONF_HONOR_EVENT_TIMES] = False
    hass.config_entries.async_update_entry(
        entry=config_entry,
        unique_id=config_entry.unique_id,
        data=data,
        version=8,
    )
    version = 8
```

### 4. Modify coordinator time resolution (`coordinator.py`)

- Import `CONF_HONOR_EVENT_TIMES` from `.const`
- Add `self.honor_event_times` in `__init__()` and `update_config()`
- Modify the time resolution block in `_ical_parser()` (lines 605–631)

The new logic:
```python
# Determine if event has explicit times (datetime vs date)
has_explicit_times = isinstance(event["DTSTART"].dt, datetime)

if self.honor_event_times and has_explicit_times:
    # FR-003: PMS times take priority for timed events
    checkin = event["DTSTART"].dt.time()
    checkout = event["DTEND"].dt.time()
elif override:
    # FR-005 (disabled) or FR-004 (all-day with override)
    start_time_val = override["start_time"].astimezone(self.timezone)
    end_time_val = override["end_time"].astimezone(self.timezone)
    checkin = start_time_val.time()
    checkout = end_time_val.time()
else:
    try:
        checkin = event["DTSTART"].dt.time()
        checkout = event["DTEND"].dt.time()
    except AttributeError:
        checkin = self.checkin
        checkout = self.checkout
```

### 5. Add UI strings (`strings.json`, translations)

Add to all three JSON files (strings.json, translations/en.json, translations/fr.json):
```json
"honor_event_times": "Honor calendar event times from PMS instead of stored override times"
```

In both `config.step.user.data` and `options.step.init.data` sections.

### 6. Add tests

- `tests/unit/test_config_flow.py`: Test toggle appears and persists
- `tests/unit/test_coordinator.py`: Test time resolution with all combinations:
  - honor=False + override → override times used
  - honor=True + timed event + override → calendar times used
  - honor=True + all-day event + override → override times used
  - honor=True + all-day event + no override → default times used
  - honor=True + timed event + no override → calendar times used
- `tests/unit/test_init.py`: Test v7→v8 migration

## Running Tests

```bash
# Run all tests
uv run pytest tests/ -v

# Run only unit tests
uv run pytest tests/unit/ -v

# Run specific test file
uv run pytest tests/unit/test_coordinator.py -v -k "honor"

# Run with coverage
uv run pytest tests/ --cov=custom_components/rental_control --cov-report=term-missing
```

## Pre-Commit Verification

```bash
# Run all hooks
pre-commit run --all-files

# Key hooks to watch for:
# - ruff (linting + formatting)
# - mypy (type checking)
# - interrogate (docstring coverage — 100% required)
# - reuse (SPDX license headers)
# - gitlint (commit message format)
```

## Key Files Reference

| File | Role |
|------|------|
| `custom_components/rental_control/const.py` | Constants: config keys, defaults |
| `custom_components/rental_control/config_flow.py` | Options flow UI schema |
| `custom_components/rental_control/__init__.py` | Entry setup, migration, update listener |
| `custom_components/rental_control/coordinator.py` | Calendar fetch, time resolution, data coordination |
| `custom_components/rental_control/event_overrides.py` | Override storage, slot reservation, time comparison |
| `custom_components/rental_control/sensors/calsensor.py` | Sensor entities, slot assignment, Keymaster side effects |
| `custom_components/rental_control/util.py` | `async_fire_update_times()`, helper functions |
