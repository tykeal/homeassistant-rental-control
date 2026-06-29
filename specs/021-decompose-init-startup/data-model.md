<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Data Model: Decompose Init Startup Readability

This feature is a behavior-preserving refactor. The models below are internal
implementation aids, not new Home Assistant options, services, entities, storage
schemas, or public runtime behavior. Package-level entry names remain importable
from `custom_components.rental_control`.

## Existing public and compatibility entities retained on `__init__.py`

### IntegrationEntryShell

**Owner module**: `custom_components.rental_control`

**Functions retained**:

- `async_setup_entry(hass, config_entry)`
- `async_unload_entry(hass, config_entry)`
- `update_listener(hass, config_entry)`
- `async_start_listener(hass, config_entry)`

**Relationships**:

- Creates and stores `RentalControlCoordinator` in
  `hass.data[DOMAIN][entry_id][COORDINATOR]`.
- Owns the `UNSUB_LISTENERS` list used by normal listeners, keymaster event
  listeners, and the startup readability watcher.
- Imports `_needs_startup_readability_refresh` from `startup_readability.py` to
  capture startup unreadability before the first refresh.
- Calls the package-level `async_arm_startup_readability_refresh` after the first
  refresh and before normal listener startup.
- Calls package-level `async_start_listener` from `update_listener` so current
  monkeypatches remain effective.

**Validation rules**:

- Setup ordering remains unchanged: Keymaster override bootstrap, startup
  unreadability capture, domain data storage, first refresh, readability arming,
  normal listener startup, platform forwarding, keymaster event listener
  registration, update-listener registration, generated-file cleanup.
- Unload cleanup continues to call every callback in `UNSUB_LISTENERS`, clear the
  list, and remove domain data only after successful platform unload.
- `update_listener` keeps safe early returns when entry data is missing before
  mutation or disappears after coordinator update.
- The package path `custom_components.rental_control.async_start_listener`
  remains patchable and affects listener restart.

### PackageCompatibilitySurface

**Owner module**: `custom_components.rental_control`

**Names retained**:

- `async_setup_entry`
- `async_unload_entry`
- `update_listener`
- `async_start_listener`
- `async_migrate_entry`
- `async_register_keymaster_listener`
- `async_arm_startup_readability_refresh`

**Relationships**:

- `async_migrate_entry` remains re-exported from `migrations.py`.
- `async_register_keymaster_listener` remains re-exported from `listeners.py`.
- `async_arm_startup_readability_refresh` is re-exported from
  `startup_readability.py`.

**Validation rules**:

- Imports from `custom_components.rental_control` continue to work for visible
  and hidden tests.
- Re-exports do not change behavior or add wrapper-only complexity.
- Existing #572 module boundaries remain intact.

## New internal startup-readability entities

### StartupReadabilityConcern

**Owner module**: `custom_components.rental_control.startup_readability`

**Purpose**: Own all logic that decides whether startup Keymaster slot entities
are readable and arms the one-shot corrective refresh when necessary.

**Functions/constants**:

- `_STARTUP_READABILITY_REFRESH_DELAY = 1.5`
- `_STARTUP_READABILITY_WATCHDOG = 10 * 60`
- `_managed_slot_readability_entity_ids(coordinator)`
- `_is_readable_keymaster_state(state)`
- `_all_managed_slots_readable(hass, entity_ids)`
- `_needs_startup_readability_refresh(hass, coordinator)`
- `async_arm_startup_readability_refresh(...)`

**Relationships**:

- Uses Home Assistant `async_track_state_change_event` for watched slot
  entities.
- Uses Home Assistant `async_call_later` for debounce and watchdog timers.
- Uses `get_entry_data(hass, entry_id)` to avoid operating after unload.
- Uses `DOMAIN` and `UNSUB_LISTENERS` to manage package-level cleanup state.
- Uses `RentalControlCoordinator.async_refresh()` for the existing one-shot
  corrective refresh.

**Validation rules**:

- No watched entities are returned when `coordinator.lockname` is falsy.
- For each managed slot, watched entities are exactly:
  `text.<lock>_code_slot_<slot>_name`,
  `text.<lock>_code_slot_<slot>_pin`, and
  `switch.<lock>_code_slot_<slot>_enabled`.
- `None` state and `STATE_UNAVAILABLE` are unreadable.
- `STATE_UNKNOWN` and any other present state remain readable.
- `_needs_startup_readability_refresh` returns `False` with the entity list when
  all watched entities are readable and `True` when any are missing or
  unavailable.

### StartupReadabilityArmRequest

**Owner module**: `startup_readability.py`

**Purpose**: The inputs to the public arming function.

**Fields/parameters**:

- `hass: HomeAssistant`
- `config_entry: ConfigEntry`
- `coordinator: RentalControlCoordinator`
- `startup_slots_unreadable: bool = False` keyword-only

**Relationships**:

- `async_setup_entry` passes the startup unreadability value captured before the
  first refresh.
- Tests may call the public function directly with
  `startup_slots_unreadable=True` to model missed transitions.
- The public function creates a `_StartupReadabilityWatcher` only when current
  state or captured startup state requires it.

**Validation rules**:

- Function signature remains compatible with current callers.
- The function returns without arming when current slots are readable and startup
  did not observe unreadability.
- The function arms when current slots are unreadable or startup previously
  observed unreadability.
- The function remains decorated with `@callback` or otherwise keeps the same
  Home Assistant callback semantics.

### _StartupReadabilityWatcher

**Owner module**: `startup_readability.py`

**Purpose**: Private lifecycle owner for the state subscription, debounce timer,
watchdog timer, unload cleanup callback, pending refresh task, and one-shot
state.

**Fields**:

- `hass: HomeAssistant`
- `config_entry: ConfigEntry`
- `coordinator: RentalControlCoordinator`
- `entity_ids: list[str]`
- `done: bool = False`
- `unsub_state: CALLBACK_TYPE | None = None`
- `unsub_timer: CALLBACK_TYPE | None = None`
- `unsub_watchdog: CALLBACK_TYPE | None = None`
- `refresh_task: asyncio.Task[Any] | None = None`

**Relationships**:

- Created by `async_arm_startup_readability_refresh`.
- Appends `remove_self` to `UNSUB_LISTENERS` for unload cleanup.
- Schedules `refresh_if_readable` through the debounce timer.
- Schedules `expire` through the watchdog timer.
- Creates the one-shot refresh task with `config_entry.async_create_task()`.

**Validation rules**:

- `arm()` subscribes to state changes before appending cleanup, starts the
  watchdog, schedules debounce if all entities are already readable, and logs
  the number of watched entities.
- `cancel_watchers()` cancels debounce, watchdog, and state subscription in the
  existing order and clears each handle.
- `remove_self()` marks the watcher done, cancels watchers, cancels a pending
  refresh task if it is not done, clears `refresh_task`, and removes the cleanup
  reference.
- `remove_listener_reference()` safely returns when entry data is already gone.
- `refresh_done()` clears `refresh_task` and removes the cleanup reference after
  the task finishes.
- `expire()` logs the same debug message and delegates to `remove_self()`.

### StartupReadabilityStateTransition

**Owner module**: watcher `schedule_refresh(event)` method

**Purpose**: Filter Home Assistant state-change events before scheduling the
readability debounce.

**Fields read from event data**:

- `old_state: State | None`
- `new_state: State | None`

**Relationships**:

- Consumes events only for `entity_ids` registered by `arm()`.
- Calls `_is_readable_keymaster_state` for old and new states.
- Replaces `unsub_timer` with a new debounce cancellation callback when a
  readable transition should be considered.

**Validation rules**:

- Do nothing when `done` is true.
- Do nothing when `new_state` is missing or unavailable.
- Do nothing when `old_state` exists and was already readable.
- Cancel any pending debounce timer before scheduling a replacement.
- Schedule the same `_STARTUP_READABILITY_REFRESH_DELAY` before rechecking all
  watched entities.

### StartupReadabilityRefreshTask

**Owner module**: watcher `refresh_if_readable`, `async_refresh_once`, and
`refresh_done` methods

**Purpose**: Execute exactly one coordinator refresh after all watched entities
are readable.

**Relationships**:

- `refresh_if_readable` is called by the debounce timer, clears `unsub_timer`,
  rechecks all managed slot entities, flips `done`, cancels remaining watchers,
  creates the task, and registers the done callback.
- `async_refresh_once` checks entry data before awaiting
  `coordinator.async_refresh()`.
- `refresh_done` removes the cleanup callback from `UNSUB_LISTENERS`.

**Validation rules**:

- No refresh is created when the watcher is done or any watched entity remains
  unreadable.
- Exactly one refresh task can be created by a watcher.
- Task name remains
  `rental_control startup readability refresh <entry_id>` using the existing
  `DOMAIN` value.
- Refresh exceptions are logged with the same non-fatal behavior and are not
  propagated.
- Entry removal before the coroutine runs skips the refresh safely.

## State Transitions

```text
idle
 └─ arm requested with current unreadable or startup unreadable
    → armed

armed
 ├─ readable transition → debounce scheduled
 ├─ all entities already readable at arm → debounce scheduled
 ├─ unload cleanup → removed
 └─ watchdog expires → removed

debounce scheduled
 ├─ another readable transition → debounce rescheduled
 ├─ timer fires and not all readable → armed
 ├─ timer fires and all readable → refreshing
 └─ unload cleanup → removed

refreshing
 ├─ refresh task finishes → complete
 └─ unload cleanup before finish → removed and pending task cancelled

complete
 └─ cleanup reference removed, no further callbacks

removed
 └─ all handles cancelled, no further refresh scheduling
```
