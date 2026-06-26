<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Data Model: Decompose Check-in Sensor

## Entities

### Check-in Tracking Sensor

The Home Assistant entity shell that remains in `sensors/checkinsensor.py`.

| Field / member | Type | Purpose |
|----------------|------|---------|
| `_hass` | `HomeAssistant` | HA event bus, service calls, and scheduling context. |
| `coordinator` | `RentalControlCoordinator` | Existing coordinator data source and metadata owner. |
| `_config_entry` | `ConfigEntry` | Cleaning window and diagnostics options. |
| `_state` | `str` | One of the four existing check-in states. |
| `_timer_manager` | `CheckinTimerManager` | Owns the single scheduled-transition unsubscribe handle. |

**Responsibilities**:

- Implement HA entity lifecycle, properties, services, and event bus emission.
- Convert current fields into `CheckinStateSnapshot` for decision helpers.
- Apply ordered decisions without changing side-effect order.
- Remain constructible by `CheckinTrackingSensor(hass, coordinator,
  config_entry)` from `custom_components/rental_control/sensor.py`.

### CheckinStateSnapshot

A typed immutable or copy-on-write snapshot of the logical state plus all fields
that are exposed as attributes or persisted by `CheckinExtraStoredData`.

| Field | Type | Maps to current source field |
|-------|------|------------------------------|
| `state` | `str` | `_state` |
| `tracked_event_summary` | `str \| None` | `_tracked_event_summary` |
| `tracked_event_start` | `datetime \| None` | `_tracked_event_start` |
| `tracked_event_end` | `datetime \| None` | `_tracked_event_end` |
| `tracked_event_slot_name` | `str \| None` | `_tracked_event_slot_name` |
| `checkin_source` | `str \| None` | `_checkin_source` |
| `checkout_source` | `str \| None` | `_checkout_source` |
| `checkout_time` | `datetime \| None` | `_checkout_time` |
| `transition_target_time` | `datetime \| None` | `_transition_target_time` |
| `checked_out_event_key` | `str \| None` | `_checked_out_event_key` |
| `next_event_start_day` | `datetime \| None` | `_next_event_start_day` |
| `checkin_lock_name` | `str \| None` | `_checkin_lock_name` |
| `linger_followon_key` | `str \| None` | `_linger_followon_key` (runtime only) |
| `linger_baseline` | `datetime \| None` | `_linger_baseline` (runtime only) |
| `event_missing_warned` | `bool` | `_event_missing_warned` (runtime only) |

**Validation rules**:

- `state` must be one of `no_reservation`, `awaiting_checkin`, `checked_in`, or
  `checked_out` unless modeling the unknown restored-state safety path.
- Persisted snapshots include only the fields currently returned by
  `CheckinExtraStoredData.as_dict()`; runtime-only fields are not persisted.
- Snapshot conversion must preserve `datetime` timezone awareness exactly as the
  current entity fields hold it.

### Persisted Extra Stored Data

The `RestoreEntity` payload implemented by `CheckinExtraStoredData`.

| Field | Type | Serialization |
|-------|------|---------------|
| `state` | `str` | string, defaults to `no_reservation` when missing |
| `tracked_event_summary` | `str \| None` | unchanged |
| `tracked_event_start` | `datetime \| None` | ISO string or `None` |
| `tracked_event_end` | `datetime \| None` | ISO string or `None` |
| `tracked_event_slot_name` | `str \| None` | unchanged |
| `checkin_source` | `str \| None` | unchanged |
| `checkout_source` | `str \| None` | unchanged |
| `checkout_time` | `datetime \| None` | ISO string or `None` |
| `transition_target_time` | `datetime \| None` | ISO string or `None` |
| `checked_out_event_key` | `str \| None` | unchanged |
| `next_event_start_day` | `datetime \| None` | ISO string or `None` |
| `checkin_lock_name` | `str \| None` | unchanged |

**Compatibility invariants**:

- `as_dict()` emits the same keys as the current source.
- `from_dict()` accepts older dictionaries with missing optional keys.
- Invalid datetime strings log the same warning and parse to `None`.
- No migration version or nested schema is required.

### CoordinatorUpdateContext

The read-only inputs needed to decide coordinator-update behavior.

| Field | Type | Purpose |
|-------|------|---------|
| `snapshot` | `CheckinStateSnapshot` | Current entity logical state. |
| `events` | `Sequence[CalendarEvent]` | Current `coordinator.data` or empty sequence. |
| `clock` | `Callable[[], datetime]` | Source-equivalent time provider. Decision code calls it at the same points where the current entity calls `dt_util.now()`, rather than collapsing a whole pass into one sampled timestamp. |
| `last_update_success` | `bool` | Existing coordinator success flag. |
| `monitoring_enabled` | `bool` | Current Keymaster monitoring status. |
| `cleaning_window_hours` | `float` | Current cleaning-window setting. |
| `event_prefix` | `str` | Prefix used by slot-name extraction. |

**Validation rules**:

- No I/O, service calls, state writes, or coordinator refreshes are performed
  while constructing or using this context.
- Event order remains the coordinator-provided order; helper functions scan it
  the same way the current source does.

### TransitionDecision

The pure result of coordinator-update decision logic.

| Field | Type | Purpose |
|-------|------|---------|
| `effects` | `tuple[DecisionEffect, ...]` | Ordered actions the entity must apply. |
| `write_state` | `bool` | Whether the current path writes HA state after effects. |
| `log_records` | `tuple[LogIntent, ...]` | Existing warning/debug messages to emit. |

Common `DecisionEffect` kinds:

- `WRITE_ONLY`: no state mutation, just preserve/write.
- `TRANSITION_TO_AWAITING`: apply event fields and schedule auto-check-in.
- `TRANSITION_TO_CHECKED_IN`: set check-in source/lock, fire check-in event
  unless restore-silent, and schedule checkout.
- `TRANSITION_TO_CHECKED_OUT`: set checkout fields, store checked-out key, fire
  checkout event unless restore-silent, and compute linger timing.
- `TRANSITION_TO_NO_RESERVATION`: clear tracked fields and cancel timer.
- `RESCHEDULE_AUTO_CHECKOUT`: cancel old timer and schedule checkout at new end.
- `RECOMPUTE_LINGER`: cancel/recompute checked-out linger timer.
- `UPDATE_TRACKED_EVENT`: update mutable event fields without changing state.

**Ordering invariants**:

- Cancel-before-replace effects occur before any new timer handle is stored.
- Multi-step decisions preserve source ordering, including checkout-then-awaiting
  self-healing.
- Restore-silent decisions never fire HA check-in/checkout bus events.

### RestoreReconciliationDecision

The pure result of validating restored data against current time and coordinator
data.

| Field | Type | Purpose |
|-------|------|---------|
| `effects` | `tuple[DecisionEffect, ...]` | Ordered silent corrections and timer intents. |
| `write_state` | `bool` | Whether restore validation writes HA state. |
| `reason` | `str` | Stale state, valid reschedule, expired linger, new event, or unknown state. |

**State-specific behavior**:

- `checked_in`: far-future tracked event or ended event becomes silent
  `checked_out`; otherwise auto-checkout is rescheduled or target cleared.
- `awaiting_checkin`: past start with monitoring off becomes silent
  `checked_in`, and then silent `checked_out` if the end also passed; otherwise
  auto-check-in is rescheduled.
- `checked_out`: new non-checked-out relevant event goes to awaiting; expired
  linger resets to no reservation; otherwise linger is recomputed.
- `no_reservation`: pending future follow-up day recreates the FR-006c timer;
  stale follow-up data is cleared.
- unknown state: warning plus reset to no reservation.

### ScheduledTransition

A pending automatic transition represented independently from the HA cancel
handle so timer behavior can be tested.

| Field | Type | Purpose |
|-------|------|---------|
| `purpose` | `auto_checkin \| auto_checkout \| linger_to_awaiting \| linger_to_no_reservation \| no_reservation_to_awaiting` | Callback path. |
| `target_time` | `datetime` | Absolute `async_track_point_in_time()` target. |
| `followon_start_day` | `datetime \| None` | FR-006c follow-up day when applicable. |
| `cancel_handle` | `CALLBACK_TYPE \| None` | Current HA unsubscribe handle, runtime only. |

**Timer invariants**:

- At most one active scheduled transition exists for the sensor.
- Replacing a timer invokes the existing `cancel_handle` exactly once before
  storing a new one.
- Callback entry clears the handle before checking current state.
- Callback state guards prevent stale callbacks from creating transitions that
  were impossible before decomposition.

## State Machine

The state names and allowed transitions remain unchanged:

```text
no_reservation ──relevant event────────────► awaiting_checkin
awaiting_checkin ──auto/keymaster checkin──► checked_in
checked_in ──auto/manual checkout──────────► checked_out
checked_out ──same-day follow-on───────────► awaiting_checkin
checked_out ──linger/different-day boundary► no_reservation
no_reservation ──FR-006c follow-up timer────► awaiting_checkin
```

Self-healing and fallback paths remain part of the same machine:

- checked-in far-future tracked event forces automatic checkout and may then
  begin awaiting a different relevant event;
- checked-in missing tracked event preserves state until stored end has passed or
  all tracking data is lost;
- awaiting missing tracked event picks the next relevant event or clears;
- checked-out excludes the checked-out event key when finding follow-ons;
- unknown restored states reset through `no_reservation`.
