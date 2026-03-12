<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Quickstart: Coordinator Base Class Migration

## What This Migration Does

Replaces the hand-rolled `RentalControlCoordinator` with one that
inherits from Home Assistant's `DataUpdateCoordinator`. This gives
the integration:

- **Automatic refresh scheduling** — no more `next_refresh` timestamp
- **Built-in error tracking** — `last_update_success` replaces custom
  flags
- **Standard entity subscription** — entities get notified via DUC
  listeners instead of manual push
- **ConfigEntryNotReady** — proper first-refresh error handling

## Migration Phases

### Phase 1: Coordinator Base Class Change

**Files**: `coordinator.py`, `__init__.py`

1. Change class to inherit from
   `DataUpdateCoordinator[list[CalendarEvent]]`
2. Call `super().__init__()` with `hass`, `logger`, `name`,
   `config_entry`, `update_interval`
3. Implement `_async_update_data()`:
   - Move fetch logic from `_refresh_calendar()`
   - Move parse logic from `_ical_parser()` (called internally)
   - Return `list[CalendarEvent]` on success
   - Raise `UpdateFailed` on error
   - Preserve miss-tracking logic
4. Implement `_async_setup()`:
   - Move Keymaster slot bootstrapping from `update()`
5. Remove `update()`, `_refresh_calendar()`, `next_refresh`,
   `calendar`, `calendar_ready`, `calendar_loaded`, `event_sensors`
6. Update `__init__.py` to call
   `async_config_entry_first_refresh()`

### Phase 2: Entity Migration

**Files**: `calendar.py`, `sensors/calsensor.py`

1. Calendar: Add `CoordinatorEntity` as base class
   - Remove manual `async_update()` that calls `coordinator.update()`
   - Read from `coordinator.data` and `coordinator.event`
   - Let `CoordinatorEntity.available` replace custom `_available`
2. Sensors: Add `CoordinatorEntity` as base class
   - Remove self-registration in `coordinator.event_sensors`
   - Override `_handle_coordinator_update()` for Keymaster side
     effects
   - Read from `coordinator.data[event_number]`

### Phase 3: Utility & Listener Updates

**Files**: `util.py`, `__init__.py`

1. Update `handle_state_change()` — `update_event_overrides()` now
   calls `async_request_refresh()` instead of setting `next_refresh`
2. Update `update_listener()` — use `coordinator.update_interval`
   setter instead of `next_refresh`
3. Remove `events_ready` / `_events_ready` references

### Phase 4: Test Updates

**Files**: All test files

1. Update coordinator tests for DUC API (`_async_update_data`,
   `UpdateFailed`, `async_config_entry_first_refresh`)
2. Update entity tests for `CoordinatorEntity` patterns
3. Update integration tests for new setup flow
4. Ensure coverage ≥ 85%

### Phase 5: Verification

1. Run full test suite
2. Run pre-commit pipeline
3. Verify no entity ID changes
4. Verify no attribute structure changes

## Key Decisions

| Decision | Rationale |
|----------|-----------|
| `list[CalendarEvent]` as data type | Maps directly to existing `self.calendar` |
| Keep `EventOverrides` as-is | Not a base class concern; collaborator pattern preserved |
| Override `_handle_coordinator_update` in sensors | Preserves Keymaster side effects (code set/clear) |
| Use `async_config_entry_first_refresh` | Standard HA pattern; auto-retries on failure |
| Preserve miss-tracking in `_async_update_data` | Custom safety net not provided by DUC |

## Running Tests

```bash
# Full test suite
uv run pytest tests/ -x -q

# Coordinator tests only
uv run pytest tests/unit/test_coordinator.py -x -v

# With coverage
uv run pytest tests/ --cov=custom_components/rental_control

# Pre-commit pipeline
uv run pre-commit run --all-files
```

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Entity ID changes | FR-010: IDs derived from config, not coordinator class |
| Sensor side effects lost | Override `_handle_coordinator_update()` |
| Miss tracking broken | Preserve in `_async_update_data()`, return stale data |
| First-load timing change | `async_config_entry_first_refresh()` runs before entities |
| CoordinatorEntity MRO issues | Tested: `(CoordinatorEntity, CalendarEntity)` resolves cleanly |
