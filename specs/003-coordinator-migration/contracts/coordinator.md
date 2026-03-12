<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Coordinator Interface Contract

## Overview

Defines the public interface of `RentalControlCoordinator` after
migration to `DataUpdateCoordinator[list[CalendarEvent]]`.

## Inherited Interface (from DataUpdateCoordinator)

These methods and properties are provided by the base class and
MUST NOT be overridden unless explicitly noted.

### Properties

- `data: list[CalendarEvent]` — Latest parsed calendar events
- `last_update_success: bool` — Whether last refresh succeeded
- `last_exception: Exception | None` — Last error encountered
- `update_interval: timedelta | None` — Refresh interval (r/w)
- `name: str` — Coordinator display name
- `config_entry: ConfigEntry` — Associated config entry

### Methods

- `async_config_entry_first_refresh() -> None`
  - Called during `async_setup_entry()`
  - Raises `ConfigEntryNotReady` on failure
  - Guarantees `data` is populated before entities exist

- `async_request_refresh() -> None`
  - Requests a debounced refresh
  - Used by `update_event_overrides()` and `update_config()`

- `async_add_listener(callback, context) -> Callable`
  - Registers an entity as a data listener
  - Returns unsubscribe callable
  - Managed automatically by `CoordinatorEntity`

- `async_update_listeners() -> None`
  - Notifies all registered listeners of new data
  - Called automatically after successful `_async_update_data()`

## Custom Interface (preserved/modified)

### Overridden Methods

- `_async_update_data() -> list[CalendarEvent]`
  - **Replaces**: `_refresh_calendar()` + `update()`
  - **Contract**: Fetches iCal URL, parses events, returns list
  - **On error**: Raises `UpdateFailed` with descriptive message
  - **On empty (miss tracking)**: Returns previous `self.data` if
    within miss tolerance; returns empty list otherwise
  - **Side effects**: Calls `event_overrides.async_check_overrides()`
    after successful parse; updates `self.event` (next event)

- `_async_setup() -> None`
  - **Replaces**: Keymaster slot bootstrapping in `update()`
  - **Contract**: Called once before first refresh
  - **Side effects**: Reads existing Keymaster entity states to
    populate `event_overrides` initial data

### Custom Properties

- `device_info: DeviceInfo` — Device registry information
  - **Contract**: Returns `(DOMAIN, unique_id)` identification
  - **Unchanged** from current implementation

- `unique_id: str` — Unique identifier
  - **Contract**: Returns config entry unique_id
  - **Unchanged**

- `event: CalendarEvent | None` — Next upcoming event
  - **Contract**: Computed from `self.data` — first event whose
    end time is after now, or `None` if no events
  - **Updated**: After each successful `_async_update_data()`

- `events_ready: bool` — All sensor entities report available
  - **Removed**: No longer needed; replaced by DUC listener model

### Custom Methods

- `async_get_events(hass, start_date, end_date) -> list[CE]`
  - **Contract**: Filters `self.data` by date range
  - **Used by**: Calendar entity's `async_get_events()`
  - **Unchanged** logic, reads from `self.data` instead of
    `self.calendar`

- `update_config(config: Mapping[str, Any]) -> None`
  - **Contract**: Updates coordinator settings from options flow
  - **Side effects**: Updates `self.update_interval`, calls
    `async_request_refresh()` for immediate refresh
  - **Changed**: No longer sets `next_refresh`; uses DUC API

- `update_event_overrides(slot, code, name, start, end) -> None`
  - **Contract**: Updates event_overrides with new slot data
  - **Side effects**: Calls `async_request_refresh()` for
    debounced refresh
  - **Changed**: No longer sets `next_refresh`; uses DUC API

- `_ical_parser(calendar, from_date, to_date) -> list[CE]`
  - **Contract**: Converts iCalendar VEVENT to CalendarEvent list
  - **Internal**: Called by `_async_update_data()`
  - **Unchanged** logic

- `_ical_event(start, end, from_date, event) -> CE | None`
  - **Contract**: Validates and creates single CalendarEvent
  - **Internal**: Called by `_ical_parser()`
  - **Unchanged** logic

- `_refresh_event_dict() -> list[CalendarEvent]`
  - **Contract**: Filters data to configured days window
  - **Changed**: Reads from `self.data` instead of `self.calendar`

## Entity Contracts

### Calendar Entity

**Base classes**: `CoordinatorEntity[RentalControlCoordinator]`,
`CalendarEntity`

- `__init__(coordinator)` — Stores coordinator reference via super()
- `event` property — Returns `self.coordinator.event`
- `available` property — Inherited from `CoordinatorEntity`
  (returns `self.coordinator.last_update_success`)
- `async_get_events()` — Delegates to coordinator
- `should_poll` — `False` (inherited from CoordinatorEntity)
- **Removed**: `async_update()` manual coordinator call

### Sensor Entity

**Base classes**: `CoordinatorEntity[RentalControlCoordinator]`

- `__init__(hass, coordinator, name, event_number)` — Stores
  coordinator via super(), keeps event_number for data indexing
- `_handle_coordinator_update()` — Override to handle Keymaster
  side effects (set/clear/update codes) before calling
  `async_write_ha_state()`
- `available` property — Inherited from `CoordinatorEntity`
- `state` — Reads from `self.coordinator.data[event_number]`
- `extra_state_attributes` — Computed from event data (unchanged)
- `should_poll` — `False` (inherited from CoordinatorEntity)
- **Removed**: Self-registration in `coordinator.event_sensors`
- **Removed**: Direct `async_update()` calls from coordinator
