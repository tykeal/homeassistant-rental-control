<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Quickstart: Code Health Implementation Guide

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

## Prerequisites

- Python ≥3.13.2
- uv package manager (used for all commands)
- Pre-commit hooks installed (`pre-commit install`)
- All tests passing: `uv run pytest tests/ -x -q`
- Ruff clean: `uv run ruff check custom_components/ tests/`

## Implementation Order

Changes are organized into four phases. Each phase builds on the
previous one. Within each phase, commits are independent and can
be applied in any order unless noted.

### Phase 1: Bug Fixes (P1 — User Stories 1 & 2)

These are the highest-priority changes addressing correctness
issues.

| # | FR | Commit Type | Scope | Files | Description |
|---|-----|-------------|-------|-------|-------------|
| 1 | 001–003 | Fix | coordinator | coordinator.py | Add try/except around `_refresh_calendar` body |
| 2 | 004 | Fix | coordinator | coordinator.py, util.py, event_overrides.py | Add `return_exceptions=True` to `asyncio.gather` calls |
| 3 | 005 | Fix | coordinator | coordinator.py | Track misses for multi-event calendars |
| 4 | 006 | Refactor | coordinator | coordinator.py | Simplify `overrides_loaded` readiness tracking |
| 5 | 007 | Fix | coordinator | coordinator.py | Remove unreachable KeyError handler (lines 521–525) |
| 6 | 008 | Fix | coordinator | coordinator.py | Log `cal_event` instance, not `CalendarEvent` class (line 535) |
| 7 | 009 | Fix | coordinator | coordinator.py | Use `is None` instead of `isinstance(x, type(None))` (line 418) |

### Phase 2: Performance (P3 — User Story 4)

| # | FR | Commit Type | Scope | Files | Description |
|---|-----|-------------|-------|-------|-------------|
| 8 | 010 | Perf | logging | __init__.py, coordinator.py, event_overrides.py, util.py | Convert 27 f-string log calls to `%s`-style |

### Phase 3: Code Modernization (P3 — User Stories 5 & 6)

Small, independent cleanup commits. Order within phase is flexible.

| # | FR | Commit Type | Scope | Files | Description |
|---|-----|-------------|-------|-------|-------------|
| 9 | 011 | Refactor | typing | config_flow.py, coordinator.py, event_overrides.py, util.py | Replace `Dict`, `List`, `Optional`, `Union` with builtins |
| 10 | 012 | Chore | imports | util.py | Remove unused `Any` import and noqa comment |
| 11 | 013 | Chore | comments | coordinator.py | Remove stale "temporary call" comment (line 356) |
| 12 | 014 | Chore | lint | multiple files | Remove inert `# pylint: disable=` directives |
| 13 | 015 | Refactor | init | __init__.py | Remove empty `CONFIG_SCHEMA` and `setup()` function |
| 14 | 016 | Refactor | config-flow | config_flow.py | Remove legacy `HANDLERS.register` decorator |
| 15 | 017 | Refactor | init | __init__.py | Replace unload gather with `async_unload_platforms()` |
| 16 | 018 | Refactor | util | util.py | Convert `os.path` calls to `pathlib.Path` |
| 17 | 019 | Chore | config-flow | config_flow.py | Remove commented-out parameter (line 135) |
| 18 | 020 | Fix | docs | event_overrides.py | Fix "EventOVerrides" docstring typo |
| 19 | 021 | Refactor | config | const.py, coordinator.py | Make `CONF_MAX_MISSES` a pure internal constant |

### Phase 4: Test Coverage (P2 — User Story 3)

Tests are added last so they cover the improved code paths.

| # | FR | Commit Type | Scope | Files | Description |
|---|-----|-------------|-------|-------|-------------|
| 20 | 022 | Test | util | tests/unit/test_util.py | Add lock slot management function tests |
| 21 | 023 | Test | coordinator | tests/unit/test_coordinator.py, tests/integration/test_error_handling.py | Add calendar error scenario tests |
| 22 | 024 | Test | coordinator | tests/unit/test_coordinator.py | Add slot bootstrapping path tests |

## Verification After Each Commit

```bash
# Run full test suite
uv run pytest tests/ -x -q

# Run linting
uv run ruff check custom_components/ tests/

# Pre-commit hooks run automatically on git commit
# DO NOT use --no-verify
```

## Final Verification

After all commits are applied:

```bash
# Full test suite with coverage
uv run pytest tests/ --cov=custom_components.rental_control \
  --cov-report=term-missing -q

# Verify coverage targets
# - Overall: ≥85% (fail_under in pyproject.toml)
# - util.py: ≥85% (up from 77%)
# - coordinator.py: ≥85% (up from 81%)

# Verify zero f-string logging
grep -rn "f['\"]" custom_components/rental_control/ | grep '_LOGGER\.'
# Expected: no output

# Verify no legacy typing imports
grep -rn 'from typing import' custom_components/rental_control/ \
  | grep -v 'TYPE_CHECKING\|Any\|Final'
# Expected: no output (only TYPE_CHECKING, Any, Final allowed)
```

## Key Patterns

### Error handling in _refresh_calendar

```python
async def _refresh_calendar(self) -> None:
    """Refresh calendar data from the iCal source."""
    try:
        # existing fetch + parse logic
        ...
    except asyncio.TimeoutError:
        _LOGGER.warning("Calendar refresh timed out for %s",
                        self.name)
    except aiohttp.ClientError as err:
        _LOGGER.warning("Calendar fetch failed for %s: %s",
                        self.name, err)
    except Exception:
        _LOGGER.exception("Unexpected error refreshing %s",
                          self.name)
```

### asyncio.gather with return_exceptions

```python
results = await asyncio.gather(*coros, return_exceptions=True)
for result in results:
    if isinstance(result, Exception):
        _LOGGER.error("Operation failed: %s", result)
```

### Lazy logging

```python
# Before (eager — always formats)
_LOGGER.debug(f"Processing event {event.summary}")

# After (lazy — formats only when debug is enabled)
_LOGGER.debug("Processing event %s", event.summary)
```
