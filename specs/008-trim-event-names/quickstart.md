<!--
SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Quickstart: Trim Event Names

## Prerequisites

- Python ≥3.14.2
- uv package manager
- Home Assistant development environment (or `pytest-homeassistant-custom-component`)

## Development Setup

```bash
# Clone and checkout feature branch
git checkout 008-trim-event-names

# Install dependencies
uv sync

# Run tests
uv run pytest tests/ -v

# Run pre-commit hooks
pre-commit run --all-files
```

## Implementation Order

### 1. Constants (`const.py`)
Add two new config key constants and their defaults:
```python
CONF_TRIM_NAMES = "trim_names"
CONF_MAX_NAME_LENGTH = "max_name_length"
DEFAULT_TRIM_NAMES = False
DEFAULT_MAX_NAME_LENGTH = 16
MIN_NAME_LENGTH = 4
```

### 2. Trim Logic (`util.py`)
Add the pure `trim_name()` function with unit tests first (TDD):
```python
def trim_name(name: str, max_length: int) -> str:
    """Trim a name to max_length on word boundaries."""
    ...
```

### 3. Config Flow (`config_flow.py`)
- Add `trim_names` (boolean) and `max_name_length` (int, min=4) to `_get_schema()`
- Add prefix-length validation warning in `_start_config_flow()`
- Bump `VERSION = 9` in `RentalControlFlowHandler`

### 4. Config Migration (`__init__.py`)
Add v8→v9 migration block in `async_migrate_entry()`.

### 5. Coordinator (`coordinator.py`)
Read `trim_names` and `max_name_length` in both `__init__()` and `update_config()`.

### 6. Integration Point (`util.py`)
Call `trim_name()` in `async_fire_set_code()` after slot_name construction.

### 7. UI Strings (`strings.json`, `translations/en.json`)
Add labels for new fields and the `prefix_too_long_for_trim` warning.

## Key Files to Modify

| File | Change |
|------|--------|
| `const.py` | New constants: `CONF_TRIM_NAMES`, `CONF_MAX_NAME_LENGTH`, defaults |
| `util.py` | New `trim_name()` function + call in `async_fire_set_code()` |
| `config_flow.py` | Schema additions + prefix warning + version bump |
| `__init__.py` | Migration v8→v9 |
| `coordinator.py` | New attributes in `__init__` + `update_config` |
| `strings.json` | UI labels + warning message |
| `translations/en.json` | English translations |

## Testing Strategy

- **Unit tests**: `trim_name()` pure function — exhaustive edge cases (empty string, exact length, single word overflow, prefix-only, whitespace-only remainder)
- **Integration tests**: Config flow with trim fields, migration v8→v9, async_fire_set_code with trimming enabled/disabled
- Run: `uv run pytest tests/ -v --tb=short`

## Pre-commit Compliance

Every commit must pass:
- `ruff check` + `ruff format` (linting/formatting)
- `mypy` (type checking — all new code needs type hints)
- `interrogate` (100% docstring coverage)
- `gitlint` (conventional commit messages)
- `reuse lint` (SPDX headers on all files)
