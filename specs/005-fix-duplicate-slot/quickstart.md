<!--
SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Quickstart: Fix Duplicate Keymaster Code Slot Assignment

## Problem

Multiple calendar sensors simultaneously read `EventOverrides.next_slot`
during a coordinator update cycle, then each independently schedule
`async_fire_set_code()` for the **same** slot number. The last writer wins,
overwriting previous guests' lock codes. This is a security issue — codes
can persist after checkout or be assigned to the wrong guest.

## Root Cause

The classic **check-then-act** race condition:

```
Sensor 0: reads next_slot = 10    ← both read before either writes
Sensor 1: reads next_slot = 10
Sensor 0: schedules set_code(10)  ← both target slot 10
Sensor 1: schedules set_code(10)
```

This happens because `_handle_coordinator_update()` is `@callback` (sync),
all sensors fire sequentially in the same event loop tick, and
`async_fire_set_code()` is scheduled as a background task that runs later.

## Solution Architecture

Four layers of defense:

1. **Serialization** — `asyncio.Lock` on `EventOverrides` serializes all
   slot mutations.

2. **Atomic Reservation** — `async_reserve_or_get_slot()` combines the
   existence check + slot write into a single lock-protected operation.

3. **Dedup Enforcement** — `async_update()` rejects writes that would
   create duplicate (name + overlapping time range) entries.

4. **Pre-execution Verification** — `async_fire_set_code()` /
   `async_fire_clear_code()` / `async_fire_update_times()` verify slot
   ownership before executing Keymaster commands.

Plus **retry/escalation** — failed lock commands are retried on
subsequent coordinator cycles with `persistent_notification` after 3
consecutive failures.

## Files to Modify

| File | Change Type | Summary |
|------|-------------|---------|
| `event_overrides.py` | **Major** | Add `asyncio.Lock`, `async_reserve_or_get_slot()`, `async_update()`, dedup enforcement, retry tracking, `verify_slot_ownership()` |
| `sensors/calsensor.py` | **Moderate** | Replace check-then-act with `async_create_task(_async_handle_slot_assignment())` |
| `util.py` | **Moderate** | Add pre-execution verification, retry tracking, persistent_notification escalation |
| `coordinator.py` | **Minor** | Adapt `update_event_overrides()` to call `async_update()` |
| `const.py` | **Minor** | Add `DEFAULT_MAX_RETRY_CYCLES = 3` |

## Key Design Decisions

| Decision | Rationale | See |
|----------|-----------|-----|
| Single lock per EventOverrides | One instance per coordinator; dedup requires scanning all slots atomically | [research.md R-001](research.md#r-001-asynciolock-placement-and-granularity) |
| Keep sync `update()` for bootstrap | No contention during `async_setup_keymaster_overrides()` | [research.md R-002](research.md#r-002-converting-sync-update-to-async) |
| Name + time overlap identity | Keymaster doesn't persist UIDs; only stable identity across restarts | [research.md R-003](research.md#r-003-identity-model--name--time-range-overlap) |
| Read-only pre-verification (no lock) | Lock during Keymaster service calls would block all slot ops for seconds | [research.md R-006](research.md#r-006-pre-execution-slot-verification) |
| Coordinator cycle as retry mechanism | No new timers; refresh interval provides natural retry opportunities | [research.md R-007](research.md#r-007-retry-and-escalation-strategy) |

## Development Workflow

### Prerequisites

```bash
# Ensure you're in the worktree
cd /home/tykeal/repos/personal/homeassistant/worktrees/slot-fix-plan

# Install dependencies
uv sync

# Verify pre-commit hooks
pre-commit run --all-files
```

### Running Tests

```bash
# All tests
uv run pytest tests/

# Just the affected test files
uv run pytest tests/unit/test_event_overrides.py tests/unit/test_util.py -v

# With coverage
uv run pytest --cov=custom_components/rental_control --cov-report=term-missing
```

### Type Checking

```bash
uv run mypy custom_components/rental_control/
```

### Suggested Implementation Order

1. **const.py** — Add `DEFAULT_MAX_RETRY_CYCLES`
2. **event_overrides.py** — Core changes (lock, reserve, dedup, retry tracking)
3. **event_overrides tests** — Unit tests for all new methods
4. **coordinator.py** — Adapt `update_event_overrides()` to async path
5. **util.py** — Pre-verification + retry/escalation
6. **util tests** — Unit tests for verification and escalation
7. **calsensor.py** — Replace check-then-act with async reservation
8. **Integration tests** — End-to-end concurrent slot assignment

Each step should be a separate atomic commit that compiles and passes tests.

## Testing Strategy

### Unit Tests (per method)

- `async_reserve_or_get_slot`: new reservation, existing match, time update,
  overflow, UID tiebreaker, concurrent calls (simulated via sequential awaits
  with lock)
- `async_update`: normal write, dedup redirect, clear slot, prefix stripping
- `async_check_overrides`: expired slot clearing, under lock, clear-code
  failure handling
- `verify_slot_ownership`: match, mismatch, empty slot
- `record_retry_failure/success`: increment, threshold, reset

### Integration Tests (end-to-end)

- **Concurrent reservation**: 5 sensors trigger simultaneously, each gets
  unique slot
- **Idempotent re-delivery**: same event re-delivered, no duplicate
- **Time update**: changed times update existing slot
- **Overflow**: more events than slots, graceful handling
- **Cleanup during assignment**: slot cleared while another is being assigned

## Reference

- **Spec**: [spec.md](spec.md) — full requirements and acceptance scenarios
- **Research**: [research.md](research.md) — design decisions with alternatives
- **Data Model**: [data-model.md](data-model.md) — entity changes and state machine
- **API Contract**: [contracts/event-overrides-api.md](contracts/event-overrides-api.md)
- **Lifecycle Contract**: [contracts/slot-lifecycle.md](contracts/slot-lifecycle.md)
