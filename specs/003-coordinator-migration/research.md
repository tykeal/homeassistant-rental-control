<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Research: Coordinator Base Class Migration

## R-001: DataUpdateCoordinator API & Lifecycle

**Decision**: Use `DataUpdateCoordinator[list[CalendarEvent]]` as the
base class, with `_async_update_data()` returning the parsed event list.

**Rationale**: The `DataUpdateCoordinator` generic type parameter
defines what `self.data` holds. Since the coordinator's primary output
is a list of `CalendarEvent` objects (currently stored as
`self.calendar`), using `list[CalendarEvent]` as the type parameter
maps directly to the existing data flow. The `data` attribute replaces
the hand-rolled `self.calendar` list.

**Alternatives considered**:

- `dict[str, Any]` (kitchen-sink data bag): Rejected — loses type
  safety and requires downstream casting.
- Custom dataclass wrapping calendar + metadata: Considered but
  deferred — adds complexity without clear benefit for this migration.
  The coordinator already stores metadata (event_overrides, sensors)
  as instance attributes, and those don't change on each refresh.
- `TimestampDataUpdateCoordinator`: Adds `last_update_success_time`
  tracking. Not needed now but trivial to adopt later since it's a
  drop-in replacement with identical API.

**Key API mapping**:

| Current Pattern | DUC Equivalent |
|----------------|----------------|
| `self.calendar` | `self.data` |
| `self.next_refresh` + manual check | `update_interval` (automatic) |
| `self.calendar_ready` / `self.calendar_loaded` | `self.last_update_success` |
| Manual `asyncio.gather(sensors)` | `async_update_listeners()` (automatic) |
| Entity calls `coordinator.update()` | Entity subscribes as listener (automatic) |
| `try/except` in `_refresh_calendar` | Raise `UpdateFailed` → DUC handles it |

## R-002: Entity Migration to CoordinatorEntity

**Decision**: Calendar entity uses `CoordinatorEntity` + `CalendarEntity`
via multiple inheritance. Sensor entities use `CoordinatorEntity` +
`Entity` (replacing bare `Entity`).

**Rationale**: `CoordinatorEntity` provides:
- `should_poll = False` (no more manual polling)
- Automatic listener registration in `async_added_to_hass()`
- `available` property tied to `coordinator.last_update_success`
- `_handle_coordinator_update()` calls `async_write_ha_state()`

The calendar entity currently calls `coordinator.update()` directly
in its `async_update()`. After migration, the DUC handles scheduling
and the entity simply reads from `coordinator.data`.

The sensor entity currently self-registers in `coordinator.event_sensors`
and is updated via `asyncio.gather()` in `_refresh_calendar()`. After
migration, sensors subscribe as DUC listeners and receive automatic
callbacks when `self.data` updates.

**MRO considerations**: Python's MRO with
`class RentalControlCalendar(CoordinatorEntity, CalendarEntity)` is
valid — `CoordinatorEntity` extends `Entity`, and `CalendarEntity`
extends `Entity`. Diamond inheritance resolves correctly via C3
linearization.

**Alternatives considered**:

- Keep entities as-is, only migrate coordinator: Rejected — defeats
  the purpose. The main benefit is the automatic listener/subscription
  model that eliminates manual sensor push updates.
- Use `BaseCoordinatorEntity` instead of `CoordinatorEntity`: Rejected
  — `CoordinatorEntity` adds the `available` property and
  `async_update()` for service calls, both of which we need.

## R-003: First Refresh Strategy

**Decision**: Use `async_config_entry_first_refresh()` in
`async_setup_entry()` to ensure data is available before entities
are created.

**Rationale**: The current code triggers the first refresh through
entity `async_update()` calls and an explicit `coordinator.update()`
in `sensor.py` setup. The DUC's `async_config_entry_first_refresh()`
provides a cleaner pattern:

1. Called in `async_setup_entry()` before forwarding platforms
2. Raises `ConfigEntryNotReady` on failure (HA retries automatically)
3. Guarantees `coordinator.data` is populated before entities exist
4. Replaces the `calendar_loaded` / `calendar_ready` flags

This is the standard HA pattern used by virtually all modern
integrations.

**Alternatives considered**:

- Manual first call to `async_refresh()`: Rejected — doesn't provide
  the `ConfigEntryNotReady` safety net.
- Lazy first fetch on entity creation: Rejected — entities may render
  with no data, causing UI flicker.

## R-004: Error Handling Migration

**Decision**: Replace the try/except blocks in `_refresh_calendar()`
with `UpdateFailed` exceptions raised from `_async_update_data()`.
Preserve the consecutive-miss logic as custom behavior.

**Rationale**: The DUC's error model is:
- `_async_update_data()` returns data on success
- `_async_update_data()` raises `UpdateFailed` on failure
- DUC automatically sets `last_update_success = False`
- DUC automatically reschedules retry
- DUC catches `TimeoutError`, `aiohttp.ClientError`, and generic
  exceptions separately

The current `_refresh_calendar()` catches `TimeoutError`,
`aiohttp.ClientError`, and `Exception`, logging each. In the DUC
model, these can be caught in `_async_update_data()` and re-raised
as `UpdateFailed` with appropriate messages.

The consecutive-miss tracking (`num_misses` / `max_misses`) is custom
logic not provided by the DUC. This must be preserved inside
`_async_update_data()`: if a new fetch returns empty but previous data
existed and misses < max, return the previous data (stale-but-valid)
without raising `UpdateFailed`.

**Alternatives considered**:

- Drop miss tracking entirely: Rejected — it's a valuable safety net
  that prevents premature data loss from transient empty responses.
- Move miss tracking to a separate middleware: Over-engineered for
  a single use case.

## R-005: Refresh Interval Configuration

**Decision**: Set `update_interval=timedelta(minutes=refresh_frequency)`
in the coordinator constructor. Update via the `update_interval`
property setter when config changes.

**Rationale**: The current code uses `self.next_refresh` timestamp
comparisons in `update()`. The DUC manages this entirely through
its internal scheduler. When the user changes the refresh interval
via options flow, the `update_listener` callback calls
`coordinator.update_interval = timedelta(minutes=new_value)` and
the DUC adjusts the schedule automatically.

The special case of `refresh_frequency == 0` (10-second startup
delay) maps to the standard `async_config_entry_first_refresh()`
pattern and is no longer needed.

**Alternatives considered**:

- Keep `next_refresh` alongside DUC scheduling: Rejected — would
  create competing schedulers and confusing behavior.

## R-006: Sensor Push Update Replacement

**Decision**: Remove `coordinator.event_sensors` list and the manual
`asyncio.gather()` sensor update call. Replace with DUC's automatic
listener notification.

**Rationale**: Currently, `_refresh_calendar()` lines 627-631 manually
iterate all registered sensors and call `async_update()` on each.
With the DUC, the flow becomes:

1. `_async_update_data()` returns new data
2. DUC stores in `self.data`
3. DUC calls `async_update_listeners()`
4. Each `CoordinatorEntity` receives `_handle_coordinator_update()`
5. Entity calls `async_write_ha_state()` → HA reads entity properties

The sensor's `async_update()` method is refactored: instead of
receiving a push call with side effects (Keymaster service calls),
the sensor reads from `coordinator.data` in its state properties
and handles Keymaster updates in `_handle_coordinator_update()`.

**Critical concern**: The current sensor `async_update()` has side
effects — it fires Keymaster service calls (`async_fire_set_code`,
`async_fire_clear_code`, `async_fire_update_times`). These side
effects must be preserved in the migration, likely by overriding
`_handle_coordinator_update()` in the sensor entity.

**Alternatives considered**:

- Keep manual sensor push alongside DUC: Rejected — creates dual
  update paths and potential race conditions.
- Move Keymaster side effects to the coordinator: Considered — would
  centralize lock management but increases coordinator complexity
  (against spec exclusion of god-class refactoring).

## R-007: Backward Compatibility Strategy

**Decision**: Preserve all entity unique IDs, names, device registry
entries, and state attribute structures exactly as-is.

**Rationale**: Entity unique IDs are set from config data (`unique_id`
property) and are independent of the coordinator base class. Device
registry entries use `(DOMAIN, unique_id)` identifiers which don't
change. State attributes are populated from event data parsing, which
remains unchanged.

The main risk areas:
- Entity `available` property: Currently custom (`self._available`
  based on `coordinator.calendar_ready`). After migration, derived
  from `coordinator.last_update_success`. Behavior should be
  equivalent since `last_update_success` tracks the same condition.
- Entity update timing: Currently push-based (coordinator calls
  sensor). After migration, callback-based (DUC notifies listeners).
  The data arrives at the same time via a different mechanism.
- First load: Currently `calendar_ready = False` until data arrives.
  After migration, `last_update_success = False` until first refresh
  succeeds, then `True`. Functionally equivalent.

## R-008: Event Overrides Integration

**Decision**: Keep `EventOverrides` as a coordinator instance attribute.
Run override checks inside `_async_update_data()` after parsing events.

**Rationale**: The current flow in `update()` is:
1. Check if refresh is due → call `_refresh_calendar()`
2. After refresh: call `event_overrides.async_check_overrides()`
3. On first load: bootstrap slots from Keymaster entities

In the DUC model:
1. `_async_update_data()` replaces both `_refresh_calendar()` and
   the override check
2. Slot bootstrapping moves to `_async_setup()` (called once before
   first refresh)
3. `update_event_overrides()` calls `async_request_refresh()` instead
   of setting `next_refresh = dt.now()`

The `event_overrides` object itself doesn't change — it remains a
collaborator that the coordinator calls during the update cycle.
