<!--
SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Research: Guest Check-in/Check-out Tracking

**Feature Branch**: `004-checkin-tracking`
**Date**: 2025-07-15

## Research Tasks

### RT-001: State Persistence via RestoreEntity

**Context**: The spec requires the check-in tracking sensor to survive HA restarts
(FR-011, FR-012). The existing `RentalControlCalSensor` is stateless — it
derives state purely from coordinator data on every update. The check-in sensor
has internal state (which event is tracked, whether we've already checked out)
that cannot be re-derived purely from calendar data.

**Decision**: Use Home Assistant's `RestoreEntity` mixin combined with
`CoordinatorEntity`.

**Rationale**:
- `RestoreEntity` provides `async_get_last_state()` and
  `async_get_last_extra_data()` for persisting arbitrary state across restarts
- The sensor class will inherit from both `CoordinatorEntity` and `RestoreSensor`
  (the sensor-specific variant that provides `SensorExtraStoredData`)
- On startup, restored state is validated against current time and coordinator
  data, transitioning if stale (FR-012)
- This is the standard HA pattern for sensors that need persistent state

**Alternatives considered**:
- **Coordinator-only (current pattern)**: Rejected — the check-in sensor has
  state that cannot be derived from calendar data alone (e.g., "was checkout
  already triggered for this event?")
- **Config entry extra data**: Rejected — not designed for frequently-changing
  sensor state; would couple sensor state to config entry lifecycle
- **Custom file storage**: Rejected — unnecessary complexity; RestoreEntity
  is the HA-blessed approach

---

### RT-002: Toggle Entity Implementation (SwitchEntity)

**Context**: FR-013 and FR-021 require toggle entities for keymaster monitoring
and early check-out expiry. The integration currently has no SwitchEntity
implementations — only Sensor and Calendar platforms.

**Decision**: Implement toggle entities as `SwitchEntity` subclasses with
`RestoreEntity` mixin, registered under a new `switch` platform.

**Rationale**:
- `SwitchEntity` is the standard HA entity for user-controllable on/off state
- `RestoreEntity` mixin persists toggle state across restarts
- Adding the `SWITCH` platform to `PLATFORMS` in `const.py` follows existing
  patterns
- Conditional creation (FR-026, FR-027): only instantiate when
  `coordinator.lockname` is not empty (keymaster is configured)

**Alternatives considered**:
- **BinarySensorEntity**: Rejected — binary sensors are read-only; toggles need
  user control
- **InputBoolean helper**: Rejected — would be external to the integration;
  cannot be conditionally created per instance
- **Config option**: Rejected — options flow changes require reload; toggles
  should be instant

---

### RT-003: Manual Check-out Action Registration

**Context**: FR-018 requires a checkout action invocable on the sensor entity.
Home Assistant supports entity-level actions via
`async_register_entity_service()`.

**Decision**: Register a `checkout` action on the sensor platform using
`async_register_entity_service()` in `sensor.py`'s `async_setup_entry()`.

**Rationale**:
- `async_register_entity_service()` binds the action to specific entity classes
- The action handler lives on the `CheckinTrackingSensor` class itself
- Guard conditions (FR-019, FR-020) are validated in the handler method
- This follows the HA convention for entity-specific actions (e.g.,
  `climate.set_temperature`, `light.turn_on`)
- The service schema is empty (no parameters needed — entity_id is implicit)

**Alternatives considered**:
- **Domain-level service** (`hass.services.async_register`): Rejected — would
  require passing entity_id as a parameter; entity services are cleaner
- **Button entity**: Rejected — buttons are fire-and-forget with no error
  feedback; actions can raise `ServiceValidationError`

---

### RT-004: Timer-Based State Transitions

**Context**: The state machine requires transitions at specific times: event
start, event end, half-gap point (FR-006a), cleaning window expiry (FR-006b),
and midnight boundary (FR-006c). These cannot rely solely on coordinator refresh
cycles (default 2 min).

**Decision**: Use Home Assistant's `async_track_point_in_time()` to schedule
precise transition callbacks, combined with coordinator update processing.

**Rationale**:
- `async_track_point_in_time()` fires a callback at a specific `datetime`,
  integrating with HA's event loop
- The sensor schedules the next transition time whenever it enters a new state
- If HA restarts, the timer is re-scheduled during state restoration
- Coordinator updates still trigger `_handle_coordinator_update()` which can
  detect missed transitions and correct state
- This dual approach (timers + coordinator updates) provides both precision and
  fault tolerance

**Alternatives considered**:
- **Coordinator-only polling**: Rejected — with 2-minute default refresh, state
  transitions could be delayed up to 2 minutes; spec says "within one coordinator
  update cycle" (SC-001) but precise timing improves UX
- **asyncio.sleep**: Rejected — does not survive HA restarts; harder to cancel
  and reschedule
- **async_call_later**: Similar to `async_track_point_in_time` but takes a
  duration rather than absolute time; less readable for calendar-based scheduling

---

### RT-005: Keymaster Lock State Changed Event Handling

**Context**: FR-014 requires listening for `keymaster_lock_state_changed` events
on the HA event bus. The current integration listens for state changes on
keymaster _entities_ (switches, text, datetime) but not for the keymaster event
bus event.

**Decision**: Register an event bus listener for `keymaster_lock_state_changed`
in `__init__.py`'s `async_setup_entry()` when keymaster is configured.

**Rationale**:
- `hass.bus.async_listen()` is the standard pattern for HA event bus listening
- The listener callback validates: event state is "unlocked",
  `code_slot_num != 0`, and `code_slot_num` falls within the managed range
  `[start_slot, start_slot + max_events)`
- The callback then delegates to the check-in sensor to attempt state transition
- The unsubscribe function is stored in `UNSUB_LISTENERS` for cleanup
- The event data expected: `{"lockname": str, "state": str, "code_slot_num": int}`

**Alternatives considered**:
- **Reuse existing state change listener**: Rejected — the existing listener
  tracks entity state changes (switch on/off, text value); the keymaster event
  is a different mechanism fired by the keymaster integration itself
- **Poll keymaster entities**: Rejected — event-driven detection is immediate;
  polling would add latency

---

### RT-006: Post-Checkout Linger Timing Strategy

**Context**: FR-006 defines three post-checkout linger scenarios with different
timing rules. The sensor must track which scenario applies and schedule the
correct transition.

**Decision**: When entering `checked_out`, compute the target transition time
based on the scenario and schedule via `async_track_point_in_time()`. Store the
target time and scenario in sensor state for persistence.

**Rationale**:
- **FR-006a (same-day turnover)**: `checkout_time + (next_start - checkout_time) / 2`
- **FR-006b (no follow-on)**: `checkout_time + cleaning_window_hours`
- **FR-006c (different-day)**: midnight after checkout day (00:00 next day)
- The "checkout time" is the actual transition time, not the event end time
  (important for manual checkouts)
- Storing the computed target in `extra_restore_state_data` ensures correct
  resumption after restart

**Alternatives considered**:
- **Re-compute on every coordinator update**: Rejected — would work but adds
  complexity to `_handle_coordinator_update`; pre-computed timers are cleaner
- **Fixed duration for all scenarios**: Rejected — spec explicitly defines
  different rules per scenario

---

### RT-007: Event Identity Tracking for FR-007

**Context**: FR-007 requires that once checked out for an event, the sensor does
NOT re-transition even if the event is extended. This means the sensor must
track which specific event it has already processed.

**Decision**: Track the currently managed event by storing a composite identity
key: `(event_summary, original_start_time, original_end_time)` as persisted
state. When an event update arrives, compare against the stored identity to
detect "same event with modified end time" vs "completely new event".

**Rationale**:
- Calendar events don't have stable unique IDs in iCalendar format (UIDs are
  optional and often missing from booking platforms)
- The combination of summary + original start time provides a sufficiently
  unique key for the rental use case (same guest, same start date)
- The `original_end_time` is stored at first association to detect extensions
- Once `checked_out` is set for an event identity, only a new event identity
  can trigger re-entry to `awaiting_checkin`

**Alternatives considered**:
- **iCalendar UID**: Rejected — not reliably present in all calendar sources;
  coordinator doesn't expose it
- **Event index only**: Rejected — event indices shift as events are added/removed
- **Summary only**: Rejected — insufficient; same guest name could appear in
  multiple events
