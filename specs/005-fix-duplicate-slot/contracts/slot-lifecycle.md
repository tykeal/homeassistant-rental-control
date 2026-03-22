<!--
SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Slot Lifecycle Contract

## Overview

This contract defines the end-to-end lifecycle of a code slot from the
perspective of the calling components (`calsensor.py`, `coordinator.py`,
`util.py`). It replaces the existing check-then-act pattern with an
atomic reservation flow.

## Lifecycle Phases

### Phase 1: Reservation (calsensor.py → event_overrides.py)

**Trigger**: `_handle_coordinator_update()` detects a new or changed event.

**Old flow** (RACE-PRONE):
```
@callback _handle_coordinator_update():
    override = overrides.get_slot_with_name(name)  # CHECK
    if override is None:
        slot = overrides.next_slot                  # READ (stale!)
        async_create_task(async_fire_set_code(slot))  # ACT (on stale slot)
```

**New flow** (ATOMIC):
```
@callback _handle_coordinator_update():
    # Detect and dispatch — no slot reads here
    if overrides and needs_slot_action:
        async_create_task(_async_handle_slot_assignment())

async _async_handle_slot_assignment():
    result = await overrides.async_reserve_or_get_slot(
        slot_name, slot_code, start_time, end_time, uid
    )
    if result.slot is None:
        return  # overflow — already logged by reserve
    if result.is_new:
        await async_fire_set_code(coordinator, self, result.slot)
    elif result.times_updated:
        # times changed — may need code update or time update
        await async_fire_update_times(coordinator, self, result.slot)
```

**Contract**:
- `_handle_coordinator_update()` MUST NOT read `next_slot` or call
  `get_slot_with_name()` to make slot assignment decisions.
- All slot assignment decisions MUST go through `async_reserve_or_get_slot()`.
- The `@callback` method MUST only extract event data and schedule the
  async handler.

---

### Phase 2: Lock Command Execution (util.py)

**Pre-execution verification** (NEW — FR-007):
```
async def async_fire_set_code(coordinator, event, slot):
    expected_name = extract_slot_name(event)
    if not coordinator.event_overrides.verify_slot_ownership(slot, expected_name):
        _LOGGER.warning("Slot %s no longer owned by %s, aborting set-code",
                        slot, expected_name)
        return
    # ... proceed with Keymaster service calls
```

**Contract**:
- Every `async_fire_set_code()`, `async_fire_clear_code()`, and
  `async_fire_update_times()` MUST verify slot ownership before the first
  Keymaster service call.
- If ownership verification fails, the operation MUST abort without modifying
  Keymaster state.
- Ownership verification is a read-only check (no lock acquisition needed).

---

### Phase 3: Retry on Failure (util.py → event_overrides.py)

**Set-code failure flow** (FR-011):
```
async def async_fire_set_code(coordinator, event, slot):
    try:
        # ... Keymaster service calls ...
        coordinator.event_overrides.record_retry_success(slot)
    except Exception:
        should_escalate = coordinator.event_overrides.record_retry_failure(slot)
        if should_escalate:
            await persistent_notification.async_create(
                coordinator.hass,
                message=f"Failed to set code for slot {slot} after "
                        f"{DEFAULT_MAX_RETRY_CYCLES} attempts. "
                        f"Manual intervention may be required.",
                title="Rental Control: Lock Code Failure",
                notification_id=f"rental_control_slot_{slot}_failure",
            )
        # Do NOT release the slot — keep reserved for next retry
        raise  # Let check_gather_results handle logging
```

**Clear-code failure flow** (FR-012):
```
async def async_fire_clear_code(coordinator, slot):
    try:
        # ... Keymaster reset ...
        coordinator.event_overrides.record_retry_success(slot)
    except Exception:
        should_escalate = coordinator.event_overrides.record_retry_failure(slot)
        if should_escalate:
            await persistent_notification.async_create(
                coordinator.hass,
                message=f"Failed to clear code for slot {slot} after "
                        f"{DEFAULT_MAX_RETRY_CYCLES} attempts. "
                        f"A stale lock code may still be active. "
                        f"Manual intervention may be required.",
                title="Rental Control: Lock Code Clear Failure",
                notification_id=f"rental_control_slot_{slot}_clear_failure",
            )
        # Do NOT mark slot as empty — keep occupied until clear confirmed
        raise
```

**Contract**:
- On set-code failure: slot MUST remain in RESERVED state, NOT released.
- On clear-code failure: slot MUST remain OCCUPIED, NOT marked as empty.
- Retry is implicit via the next coordinator update cycle.
- Escalation MUST use `persistent_notification.async_create()` with a stable
  `notification_id` (prevents duplicate notifications).
- On success after previous failures: MUST call `record_retry_success()` to
  clear counters and dismiss the notification.

---

### Phase 4: Override Check / Cleanup (event_overrides.py)

**Flow within coordinator update cycle**:
```
coordinator._async_update_data():
    calendar = await fetch_calendar()
    if event_overrides:
        await event_overrides.async_check_overrides(self, calendar)
    return calendar
    # → framework updates self.data
    # → framework notifies sensors
    # → sensors' _handle_coordinator_update() fires
```

**Contract**:
- `async_check_overrides()` MUST hold the lock for its entire iteration
  (FR-008).
- Clearing a slot MUST both: (a) call `async_fire_clear_code()` and
  (b) write `None` to `_overrides[slot]`.
- Both (a) and (b) MUST happen within the same lock acquisition.
- If `async_fire_clear_code()` fails, the slot MUST NOT be cleared from
  `_overrides` — it stays occupied for retry on the next cycle (FR-012).

---

## Ordering Guarantees

```
Within a single coordinator update cycle:

  1. async_check_overrides() runs (under lock)
     → clears expired/invalid slots
     → next_slot recalculated

  2. framework updates coordinator.data

  3. All sensors' _handle_coordinator_update() fire sequentially
     → each schedules _async_handle_slot_assignment()

  4. Scheduled tasks execute
     → each awaits async_reserve_or_get_slot() (serialized by lock)
     → each receives unique slot (or overflow)
     → each executes Keymaster commands (verified before execution)
```

**Key guarantee**: Between steps 3 and 4, the lock ensures that
`async_reserve_or_get_slot()` calls are serialized. Even though all sensors
schedule their tasks nearly simultaneously, the lock ensures each task sees
the result of the previous task's reservation.
