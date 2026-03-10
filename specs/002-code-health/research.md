<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Phase 0 Research: Code Health Improvement

**Date**: 2026-03-10
**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

## Research Questions & Decisions

### R1: How to handle calendar fetch/parse errors (FR-001–003)

**Decision**: Wrap the entire `_refresh_calendar` method body in a
try/except that catches `Exception` (with specific handling for
`asyncio.TimeoutError`, `aiohttp.ClientError`, and ical parsing
errors). On failure, log the error and return early, preserving the
existing `self.calendar` data.

**Rationale**: The coordinator already maintains `self.calendar` as
the "known good" state. The simplest approach is to catch errors
before any mutation of `self.calendar` occurs. This matches HA
patterns where coordinators preserve last-known-good data on update
failures.

**Alternatives considered**:
- Per-operation try/except around each step (HTTP fetch, timezone
  conversion, ical parsing separately): More granular but adds
  complexity for little benefit — if any step fails, we want the
  same outcome (keep previous data).
- Returning a Result/Either type: Overengineered for this use case.

### R2: asyncio.gather error handling strategy (FR-004)

**Decision**: Add `return_exceptions=True` to all `asyncio.gather`
call sites, then check results for exceptions and log them.

**Rationale**: Without `return_exceptions=True`, a single failing
coroutine causes `asyncio.gather` to cancel the remaining
coroutines. For lock slot operations, this means a failing "set pin"
would silently prevent "set name" and "set dates" from executing.
With `return_exceptions=True`, all coroutines complete and any
exceptions are returned as values in the result list.

**Alternatives considered**:
- `asyncio.TaskGroup`: Requires Python 3.11+ (available) but uses
  different cancellation semantics — a single failure cancels the
  group, which is the opposite of what we want.
- Sequential execution: Would work but loses the parallelism benefit
  for independent service calls.

**Call sites to update**:
1. `util.py` — `async_fire_set_code`
2. `util.py` — `handle_state_change` (gather for service calls)
3. `event_overrides.py` — `async_check_overrides`
4. `coordinator.py` — `_refresh_calendar` sensor update gather
5. `__init__.py` — `async_unload_entry` (addressed separately by
   FR-017 which replaces the gather entirely)

### R3: Multi-event empty refresh handling (FR-005)

**Decision**: Remove the conditional that skips miss tracking when
`len(calendar) > 1`. Apply the same `max_misses` tracking logic
regardless of calendar size.

**Rationale**: The current code only tracks misses for single-event
calendars. Multi-event calendars that receive an empty refresh will
silently preserve stale data forever. The miss tracking mechanism
already exists — it just needs to be applied uniformly.

**Alternatives considered**:
- Different miss thresholds for multi-event calendars: No clear
  reason to treat them differently.

### R4: Calendar readiness state simplification (FR-006)

**Decision**: Review the dual-path readiness tracking
(`calendar_ready` vs `overrides_loaded`) and ensure there is a
single clear code path for determining when the coordinator is
ready to process events, regardless of whether a lock manager is
configured.

**Rationale**: The current code sets `overrides_loaded = True` only
when `lockname is None` in `_refresh_calendar`, creating an implicit
coupling between lock manager configuration and calendar readiness.
This should be explicit and testable.

**Alternatives considered**:
- Full coordinator refactor: Out of scope per exclusions.

### R5: Lazy logging approach (FR-010)

**Decision**: Replace all 27 f-string log calls with `%s`-style
deferred formatting. Use `_LOGGER.debug("message %s", var)` pattern
consistently.

**Rationale**: When a log level is disabled (e.g., debug in
production), f-string formatting still executes the string
interpolation. With `%s`-style, the formatting is deferred to the
logging framework and skipped entirely when the level is inactive.

**Alternatives considered**:
- `_LOGGER.isEnabledFor()` guards: More verbose, harder to
  maintain, and `%s`-style achieves the same result.

### R6: Legacy HA pattern replacement (FR-015–017)

**Decision**:
- **FR-015**: Remove `CONFIG_SCHEMA` and `setup()` entirely. The
  integration is config-flow only (declared in manifest.json).
- **FR-016**: Remove `@config_entries.HANDLERS.register(DOMAIN)`
  decorator. The manifest `config_flow: true` key handles
  registration.
- **FR-017**: Replace the `asyncio.gather` loop of individual
  `async_forward_entry_unload()` calls with a single
  `config_entry.async_unload_platforms(hass, PLATFORMS)` call.

**Rationale**: These are all documented HA deprecations. The modern
equivalents are simpler and reduce code.

**Alternatives considered**: None — these are straightforward
pattern replacements with clear HA documentation.

### R7: pathlib migration scope (FR-018)

**Decision**: Convert `os.path` usage in `util.py` to `pathlib.Path`
operations. The test suite already uses `pathlib` via
`tmp_path` fixtures, so this aligns source with tests.

**Rationale**: `pathlib` is the modern Python approach for file
operations, provides a more readable API, and is already the style
used in the test suite.

**Alternatives considered**:
- Leaving `os.path` as-is: Inconsistent with test code style.

### R8: CONF_MAX_MISSES handling (FR-021)

**Decision**: Treat `CONF_MAX_MISSES` as a pure internal constant.
Remove any configuration read for it and reference the constant
directly. Do not expose it in the config flow UI.

**Rationale**: The config flow already does not expose this value.
Making it a pure constant simplifies the code and eliminates the
hybrid config/constant pattern.

**Alternatives considered**:
- Exposing in config flow: Adds UI complexity for a niche setting
  that most users would never touch.

### R9: Test coverage strategy (FR-022–024)

**Decision**: Add targeted tests for the three identified coverage
gaps:
1. **Lock slot management** (util.py lines 107–249): Test
   `async_fire_set_code`, `async_fire_clear_code`,
   `async_fire_update_times`, and `handle_state_change` using
   mocked Keymaster service calls.
2. **Calendar error scenarios** (coordinator.py
   `_refresh_calendar`): Test timeout, malformed ical, timezone
   conversion failure, non-200 HTTP responses using aioresponses.
3. **Slot bootstrapping** (coordinator.py lines 241–314): Test
   the Keymaster entity discovery and slot initialization during
   coordinator startup.

**Rationale**: These are the highest-risk uncovered paths. Lock
slot functions are at 0% coverage and handle real lock hardware
interactions. Calendar errors are the most common production
failure mode.

**Alternatives considered**:
- Full integration tests: Would require a running HA instance;
  unit tests with mocks are sufficient for these paths.
