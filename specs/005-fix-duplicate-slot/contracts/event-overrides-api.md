<!--
SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# EventOverrides Internal API Contract

## Overview

This contract defines the internal Python API surface for slot management
after the duplicate-slot fix. All methods are on the `EventOverrides` class
in `custom_components/rental_control/event_overrides.py`.

## Lock Protocol

All async mutation methods acquire `self._lock` internally. Callers MUST NOT
acquire the lock externally — doing so would cause deadlock (asyncio.Lock is
not reentrant).

```
RULE: Only EventOverrides methods acquire _lock.
RULE: Callers await async methods; they never touch _lock directly.
RULE: sync update() does NOT acquire _lock (bootstrap-only, no contention).
```

## Methods

### async_reserve_or_get_slot()

```python
async def async_reserve_or_get_slot(
    self,
    slot_name: str,
    slot_code: str,
    start_time: datetime,
    end_time: datetime,
    uid: str | None = None,
    prefix: str | None = None,
) -> ReserveResult:
    """Atomically find an existing slot or reserve the next available.

    Args:
        slot_name: Guest name (raw, may include prefix).
        slot_code: Generated door code.
        start_time: Check-in time (UTC).
        end_time: Check-out time (UTC).
        uid: CalendarEvent UID (runtime-only tiebreaker, optional).
        prefix: Event prefix to strip from slot_name.

    Returns:
        ReserveResult(slot, is_new, times_updated):
          - slot: Assigned slot number, or None if no slots available.
          - is_new: True if a new slot was reserved (caller should set-code).
          - times_updated: True if existing slot's times were changed.

    Lock: Acquires and releases self._lock internally.

    Guarantees:
      - At most one slot per (name, overlapping time range) pair.
      - next_slot is recalculated before lock release.
      - Overflow logged if no slot available (FR-010).
    """
```

**Caller**: `RentalControlCalSensor._async_handle_slot_assignment()`

**Postconditions**:
- If `is_new`: `_overrides[slot]` is populated, `_next_slot` recalculated.
- If `times_updated`: `_overrides[slot]["start_time"]` and `["end_time"]`
  updated.
- If `slot is None`: all slots occupied, overflow logged.

---

### async_update()

```python
async def async_update(
    self,
    slot: int,
    slot_code: str,
    slot_name: str,
    start_time: datetime,
    end_time: datetime,
    prefix: str | None = None,
) -> None:
    """Update a specific slot with dedup enforcement.

    If slot_name with overlapping times already exists in a DIFFERENT slot,
    redirects to the existing slot and logs a warning (FR-004).

    Args:
        slot: Target slot number.
        slot_code: Door code (empty string to clear).
        slot_name: Guest name (empty string to clear).
        start_time: Check-in time.
        end_time: Check-out time.
        prefix: Event prefix to strip.

    Lock: Acquires and releases self._lock internally.

    Guarantees:
      - Slot identity invariant maintained (no duplicate name+overlap).
      - _next_slot recalculated after write.
    """
```

**Callers**: `async_check_overrides()`, `coordinator.update_event_overrides()`

---

### update() (bootstrap only)

```python
def update(
    self,
    slot: int,
    slot_code: str,
    slot_name: str,
    start_time: datetime,
    end_time: datetime,
    prefix: str | None = None,
) -> None:
    """Update a specific slot WITHOUT lock acquisition.

    WARNING: This method MUST only be called during bootstrap
    (async_setup_keymaster_overrides) when no concurrent access is
    possible. After bootstrap, use async_update() instead.
    """
```

---

### async_check_overrides()

```python
async def async_check_overrides(
    self,
    coordinator: RentalControlCoordinator,
    calendar: list[CalendarEvent] | None = None,
) -> None:
    """Validate all assigned slots against current calendar; clear invalid.

    Lock: Acquires self._lock for the entire check-and-clear cycle (FR-008).

    Flow:
      1. Acquire lock.
      2. Snapshot assigned slots.
      3. For each slot: evaluate against calendar events.
      4. For invalid slots: call async_fire_clear_code(), then clear in _overrides.
      5. Release lock.
    """
```

---

### record_retry_failure() / record_retry_success()

```python
def record_retry_failure(self, slot: int) -> bool:
    """Record a failed lock command attempt for the given slot.

    Returns True if the failure count has reached the escalation threshold
    (DEFAULT_MAX_RETRY_CYCLES) and escalation has not yet been sent.

    Does NOT acquire _lock (counter operations are atomic in single-threaded
    asyncio — no await between read and write).
    """

def record_retry_success(self, slot: int) -> None:
    """Reset failure tracking for the given slot.

    Clears retry count and escalation flag.
    """
```

**Callers**: `async_fire_set_code()`, `async_fire_clear_code()` in `util.py`

---

### verify_slot_ownership()

```python
def verify_slot_ownership(self, slot: int, expected_name: str) -> bool:
    """Check if the given slot is still assigned to expected_name.

    Does NOT acquire _lock (read-only, stale reads are safe — see
    research.md R-006).

    Returns True if slot exists and slot_name matches expected_name.
    """
```

**Callers**: `async_fire_set_code()`, `async_fire_clear_code()`,
`async_fire_update_times()` in `util.py`

---

## Error Taxonomy

| Condition | Method | Behavior |
|-----------|--------|----------|
| All slots occupied | `async_reserve_or_get_slot` | Returns `ReserveResult` with `slot=None` and both flags `False`, logs warning |
| Duplicate name+overlap | `async_update` | Redirects to existing slot, logs warning |
| Slot ownership mismatch | `verify_slot_ownership` | Returns `False`, caller aborts + logs |
| Retry threshold reached | `record_retry_failure` | Returns `True`, caller creates notification |
