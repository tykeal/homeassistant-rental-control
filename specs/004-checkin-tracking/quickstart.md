<!--
SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Quickstart: Guest Check-in/Check-out Tracking

**Feature Branch**: `004-checkin-tracking`
**Date**: 2025-07-15

## Prerequisites

- Python 3.13.2+ (per pyproject.toml)
- uv package manager installed
- Home Assistant development environment (`homeassistant>=2025.8.0`)

## Development Setup

```bash
# Clone and enter worktree
git clone https://github.com/tykeal/homeassistant-rental-control.git
cd homeassistant-rental-control
git switch 004-checkin-tracking

# Install dependencies
uv sync --all-extras

# Run existing tests to verify baseline
uv run pytest tests/ -v
```

## New Files to Create

### Core Implementation

| File | Purpose |
|------|---------|
| `custom_components/rental_control/sensors/checkinsensor.py` | Check-in tracking sensor entity (state machine, persistence, transitions) |
| `custom_components/rental_control/switch.py` | Switch platform setup + KeymasterMonitoringSwitch + EarlyCheckoutExpirySwitch |

### Tests

| File | Purpose |
|------|---------|
| `tests/unit/test_checkin_sensor.py` | Unit tests for state machine transitions, event identity, attribute exposure |
| `tests/unit/test_switch.py` | Unit tests for toggle entities, conditional creation, restore |
| `tests/integration/test_checkin_tracking.py` | Integration tests for full lifecycle including keymaster interaction |

## Files to Modify

| File | Change |
|------|--------|
| `custom_components/rental_control/const.py` | Add `CONF_CLEANING_WINDOW`, new event names, checkin state constants |
| `custom_components/rental_control/sensor.py` | Add `CheckinTrackingSensor` creation, register checkout service |
| `custom_components/rental_control/__init__.py` | Add keymaster event bus listener, store reference for sensor access |
| `custom_components/rental_control/config_flow.py` | Add cleaning window option to options flow |
| `custom_components/rental_control/strings.json` | Add translations for new entities and config options |
| `custom_components/rental_control/translations/en.json` | English translations |

## Architecture Overview

```text
                    ┌─────────────────────────┐
                    │   Coordinator (existing) │
                    │   - Calendar fetch       │
                    │   - Event parsing        │
                    │   - Override management  │
                    └────────┬────────────────┘
                             │ data updates
                    ┌────────▼────────────────┐
                    │  CheckinTrackingSensor   │
                    │  (NEW - one per instance)│
                    │  - State machine         │
                    │  - Timer scheduling      │
                    │  - Event identity        │
                    │  - RestoreEntity         │
                    └────────┬────────────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
     ┌────────▼──┐   ┌──────▼──────┐  ┌───▼──────────┐
     │ HA Events │   │ Keymaster   │  │  Toggle       │
     │ checkin/  │   │ Event Bus   │  │  Switches     │
     │ checkout  │   │ Listener    │  │  (NEW)        │
     └───────────┘   └─────────────┘  └──────────────┘
```

## Quick Verification

After implementation, verify with:

```bash
# Run all tests
uv run pytest tests/ -v

# Run only new tests
uv run pytest tests/unit/test_checkin_sensor.py tests/unit/test_switch.py -v

# Run with coverage
uv run pytest tests/ --cov=custom_components/rental_control --cov-report=term-missing

# Lint check
uv run ruff check custom_components/rental_control/
uv run ruff format --check custom_components/rental_control/

# Type check
uv run mypy custom_components/rental_control/

# Full pre-commit
pre-commit run --all-files
```
