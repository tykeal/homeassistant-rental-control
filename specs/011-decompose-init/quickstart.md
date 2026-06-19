<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Quickstart: Decompose Integration Entry Module

**Feature Branch**: `011-decompose-init`
**Date**: 2026-06-19

## Overview

This refactor moves migration logic to `migrations.py` and keymaster event-bus
listener logic to `listeners.py` while preserving all package-level Home
Assistant entry points and existing runtime behavior.

## Implementation Scope

### Files Expected to Change

| File | Change |
|------|--------|
| `custom_components/rental_control/__init__.py` | Remove detailed migration and keymaster event-listener bodies, import/re-export moved public functions, keep setup/unload/update/start orchestration |
| `custom_components/rental_control/migrations.py` | Add `async_migrate_entry()` and private per-version helpers with SPDX, docstrings, and type hints |
| `custom_components/rental_control/listeners.py` | Add `async_register_keymaster_listener()` and private event/diagnostic/forwarding helpers with SPDX, docstrings, and type hints |
| `tests/unit/test_init.py` | Keep package-level import assertions/coverage; adjust only if needed for module-boundary checks |
| `tests/unit/test_keymaster_event_diagnostics.py` | Existing diagnostics behavior should pass unchanged |
| `tests/unit/test_checkin_sensor.py` | Existing keymaster forwarding/rejection behavior should pass unchanged |

### Files Not Expected to Change

| Path | Why |
|------|-----|
| `specs/011-decompose-init/data-model.md` | No new data model is introduced |
| `specs/011-decompose-init/contracts/` | No API, service, entity, or configuration contract changes are introduced |
| Agent context files | No new technology, dependency, or platform context is introduced |

## Maintainer Verification

### 1. Confirm Package-Level Imports

Verify Home Assistant and tests can still import the public entry points from the
integration package:

```bash
uv run python - <<'PY'
from custom_components.rental_control import async_migrate_entry
from custom_components.rental_control import async_register_keymaster_listener
from custom_components.rental_control import async_setup_entry
from custom_components.rental_control import async_start_listener
from custom_components.rental_control import async_unload_entry
from custom_components.rental_control import update_listener

for obj in (
    async_setup_entry,
    async_unload_entry,
    async_migrate_entry,
    update_listener,
    async_start_listener,
    async_register_keymaster_listener,
):
    assert callable(obj), obj
PY
```

Expected result: the script exits successfully.

### 2. Confirm Complexity Thresholds

Confirm `__init__.py` is under 400 lines and no functions in the affected files
are over 80 lines:

```bash
uv run python - <<'PY'
import ast
from pathlib import Path

paths = [
    Path("custom_components/rental_control/__init__.py"),
    Path("custom_components/rental_control/migrations.py"),
    Path("custom_components/rental_control/listeners.py"),
]
init_lines = len(paths[0].read_text().splitlines())
assert init_lines < 400, init_lines
for path in paths:
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = min((d.lineno for d in node.decorator_list), default=node.lineno)
            length = node.end_lineno - start + 1
            assert length <= 80, f"{path}:{node.name} is {length} lines"
PY
```

Expected result: the script exits successfully.

### 3. Run aislop

Confirm the issue-reported warnings no longer appear for the entry module through
the repository's Node-based pre-commit hook. Stage the implementation files first
because the hook runs `aislop ci --staged`:

```bash
git add \
  custom_components/rental_control/__init__.py \
  custom_components/rental_control/migrations.py \
  custom_components/rental_control/listeners.py
uv run pre-commit run aislop
```

Expected result: `custom_components/rental_control/__init__.py` is not reported
for `complexity/file-too-large`, and neither `async_migrate_entry` nor
`async_register_keymaster_listener` is reported for
`complexity/function-too-long`.

### 4. Run the Existing Test Suite Unchanged

Run the full existing suite as the behavior baseline:

```bash
uv run pytest tests/
```

Expected result: all existing tests pass without assertion changes.

### 5. Run Linting

Run ruff on the changed implementation and test files:

```bash
uv run ruff check \
  custom_components/rental_control/__init__.py \
  custom_components/rental_control/migrations.py \
  custom_components/rental_control/listeners.py \
  tests/unit/test_init.py \
  tests/unit/test_keymaster_event_diagnostics.py \
  tests/unit/test_checkin_sensor.py
```

Expected result: ruff passes. Pre-commit hooks and CI must also pass before the
implementation PR is merged.
