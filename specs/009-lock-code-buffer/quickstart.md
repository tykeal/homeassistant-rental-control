# Quickstart: Lock Code Buffer Times

**Feature Branch**: `009-lock-code-buffer`
**Date**: 2025-07-17

## Overview

This feature adds two new configuration options — "code buffer before" and "code buffer after" — that offset the lock code validity window sent to Keymaster. A before-buffer of 30 minutes means a 3:00 PM check-in produces a lock code valid from 2:30 PM. An after-buffer of 15 minutes means an 11:00 AM checkout keeps the code active until 11:15 AM.

## Implementation Scope

### Files to Modify (~6 files)

| File | Change |
|------|--------|
| `const.py` | Add `CONF_CODE_BUFFER_BEFORE`, `CONF_CODE_BUFFER_AFTER`, `DEFAULT_CODE_BUFFER_BEFORE`, `DEFAULT_CODE_BUFFER_AFTER` |
| `__init__.py` | Add v9→v10 migration |
| `coordinator.py` | Add `code_buffer_before`/`code_buffer_after` properties to `__init__` and `update_config` |
| `config_flow.py` | Add buffer fields to `_get_schema` (conditional on lock entry); bump VERSION to 10 |
| `util.py` | Apply buffer offsets in `async_fire_set_code` and `async_fire_update_times` |
| `strings.json` | Add labels and descriptions for both buffer fields |

### Files NOT Modified

| File | Why |
|------|-----|
| `event_overrides.py` | FR-005: overrides use unbuffered times |
| `sensors/calsensor.py` | Event attributes remain unbuffered |
| `sensors/checkinsensor.py` | Check-in timing uses raw event times |

## Key Design Decisions

1. **Buffer applied at Keymaster call site only**: The offset is computed in `async_fire_set_code`/`async_fire_update_times` right before the service call. This prevents contamination of all other consumers.

2. **Conditional visibility in config flow**: Buffer fields only appear when a lock entry is selected (FR-008). Implemented by conditionally extending the schema based on `CONF_LOCK_ENTRY` value.

3. **No upper bound validation**: Per spec edge cases, large buffers are legitimate (e.g., 24-hour buffers for cleaning crews).

4. **Lazy update on refresh**: When buffer values change, active slots are updated on the next coordinator refresh cycle — consistent with `trim_names` pattern.

## Development Workflow

### Prerequisites

```bash
# From repo root
uv sync  # install dependencies
```

### Run Tests

```bash
uv run pytest tests/ -v
```

### Commit Pattern (Atomic Commits)

Suggested commit sequence:

1. `Feat: add CONF_CODE_BUFFER constants to const.py`
2. `Feat: add v9-to-v10 config migration for buffer fields`
3. `Feat: add buffer properties to coordinator`
4. `Feat: add buffer fields to config flow schema`
5. `Feat: apply buffer offsets in Keymaster service calls`
6. `Feat: add strings.json labels for buffer fields`
7. `Test: add unit tests for buffer functionality`

Each commit must pass all pre-commit hooks (`ruff`, `mypy`, `interrogate`, `reuse-tool`, `gitlint`).

## Testing Strategy

| Test Area | File | What to Test |
|-----------|------|-------------|
| Constants | `test_util.py` or new | Constants exist with correct defaults |
| Migration | `test_init.py` | v9→v10 adds both fields with default 0 |
| Coordinator | `test_coordinator.py` | Properties initialized from config; update_config picks up changes |
| Config flow | `test_config_flow.py` | Buffer fields present when lock configured; absent when not; validation rejects negative values |
| Buffer logic | `test_util.py` | `async_fire_set_code` sends buffered start/end to Keymaster; 0 buffer sends unbuffered times |
| Integration | `test_refresh_cycle.py` | Buffer change propagates to active slots on refresh |
