<!--
SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Data Model: Fix Duplicate Keymaster Code Slot Assignment

## Entities

### EventOverride (existing — unchanged)

```python
class EventOverride(TypedDict):
    """Event override definition."""

    slot_name: str       # Guest name (extracted via get_slot_name())
    slot_code: str       # Generated door code
    start_time: datetime  # Check-in time (UTC)
    end_time: datetime    # Check-out time (UTC)
```

**Persistence boundary**: Reconstructed from Keymaster entity state on HA
restart. Only `slot_name`, `slot_code`, `start_time`, `end_time` survive.

### EventOverrides (existing — modified)

```python
class EventOverrides:
    # Existing fields
    _max_slots: int
    _next_slot: int | None
    _overrides: dict[int, EventOverride | None]
    _ready: bool
    _start_slot: int

    # NEW fields
    _lock: asyncio.Lock                          # FR-002: serialization lock
    _retry_counts: dict[int, int]                # FR-011/012: per-slot failure counter
    _escalated: dict[int, bool]                  # FR-011/012: per-slot escalation flag
```

**Field details**:

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `_lock` | `asyncio.Lock` | `asyncio.Lock()` | Serializes all slot read-modify-write operations (FR-002) |
| `_retry_counts` | `dict[int, int]` | `{}` | Tracks consecutive failed set-code/clear-code attempts per slot number. Reset to 0 on success. |
| `_escalated` | `dict[int, bool]` | `{}` | Tracks whether a persistent_notification has been sent for a slot. Prevents duplicate notifications. Reset when retry succeeds. |

### ReserveResult (new — return type)

```python
class ReserveResult(NamedTuple):
    """Result of a slot reservation attempt."""

    slot: int | None      # Assigned slot number, or None if no slot available
    is_new: bool          # True if this is a new reservation (needs set-code)
    times_updated: bool   # True if existing slot's times were changed
```

**Usage**: Returned by `async_reserve_or_get_slot()`. Callers branch on
`is_new` / `times_updated` to determine which lock command to issue.

## Relationships

```text
RentalControlCoordinator (1)
    │
    ├── event_overrides: EventOverrides (1)
    │       │
    │       ├── _overrides: dict[int, EventOverride | None]
    │       │     Key: slot number (start_slot .. start_slot + max_slots - 1)
    │       │     Value: EventOverride or None (empty slot)
    │       │
    │       ├── _lock: asyncio.Lock
    │       │     Scope: all slot mutations after bootstrap
    │       │
    │       └── _retry_counts / _escalated: dict[int, int/bool]
    │             Scope: per-slot failure tracking
    │
    ├── data: list[CalendarEvent] (N)
    │     Updated each coordinator refresh cycle
    │
    └── sensors: list[RentalControlCalSensor] (N, up to max_events)
          Each sensor monitors data[event_number]
```

## Validation Rules

### Slot Identity Invariant (FR-001, FR-004)

```
INVARIANT: For all pairs (slot_a, slot_b) where slot_a ≠ slot_b:
  IF _overrides[slot_a] is not None AND _overrides[slot_b] is not None:
    NOT (
      _overrides[slot_a]["slot_name"] == _overrides[slot_b]["slot_name"]
      AND times_overlap(
        _overrides[slot_a]["start_time"], _overrides[slot_a]["end_time"],
        _overrides[slot_b]["start_time"], _overrides[slot_b]["end_time"]
      )
    )
  UNLESS: runtime UIDs are available AND differ (proving distinct reservations)
```

### Time Range Overlap Definition

```python
def times_overlap(start_a: datetime, end_a: datetime,
                  start_b: datetime, end_b: datetime) -> bool:
    """Two time ranges overlap if they share any point in time."""
    return start_a < end_b and start_b < end_a
```

### Slot Capacity (FR-010)

```
CONSTRAINT: count(slot for slot in _overrides.values() if slot is not None)
            <= _max_slots

When violated: log overflow warning, return ReserveResult(None, False, False) from reserve.
```

### Retry Threshold (FR-011, FR-012)

```
RULE: _retry_counts[slot] >= DEFAULT_MAX_RETRY_CYCLES (3)
      → create persistent_notification
      → set _escalated[slot] = True
      → continue retries

RULE: On successful set-code or clear-code:
      → _retry_counts[slot] = 0
      → dismiss persistent_notification
      → _escalated[slot] = False
```

## State Transitions

### Slot Lifecycle

```
                    ┌──────────┐
                    │  EMPTY   │ _overrides[slot] = None
                    └────┬─────┘
                         │ async_reserve_or_get_slot()
                         │ (lock acquired, name+time checked, next_slot assigned)
                         ▼
                    ┌──────────┐
                    │ RESERVED │ _overrides[slot] = EventOverride{...}
                    └────┬─────┘
                         │ async_fire_set_code() succeeds
                         │ (pre-verified, Keymaster configured)
                         ▼
                    ┌──────────┐
                    │  ACTIVE  │ Lock code set on physical lock
                    └────┬─────┘
                         │
              ┌──────────┼──────────┐
              │          │          │
              ▼          ▼          ▼
        ┌──────────┐ ┌─────────┐ ┌────────────┐
        │ UPDATING │ │EXPIRING │ │SET-CODE    │
        │  TIMES   │ │         │ │RETRY       │
        └────┬─────┘ └────┬────┘ └─────┬──────┘
             │            │            │ (retry_count < threshold)
             │            │            │ → retry on next coordinator cycle
             │            │            │ (retry_count >= threshold)
             │            │            │ → persistent_notification + continue
             ▼            ▼            ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐
        │  ACTIVE  │ │ CLEARING │ │  ACTIVE  │ (eventually succeeds)
        │ (updated)│ │          │ └──────────┘
        └──────────┘ └────┬─────┘
                          │ async_fire_clear_code() succeeds
                          │ (pre-verified, Keymaster reset)
                          ▼
                    ┌──────────┐
                    │  EMPTY   │ _overrides[slot] = None
                    └──────────┘
                          │
                    (CLEAR-CODE RETRY path mirrors SET-CODE RETRY:
                     keep slot occupied, retry, escalate after threshold)
```

### Reservation Decision Tree

```
async_reserve_or_get_slot(name, code, start, end, uid=None):
    │
    ├── _find_overlapping_slot(name, start, end, uid)
    │       │
    │       ├── Found existing slot S with same name + overlapping times
    │       │   └── Same UID or UID unavailable?
    │       │       ├── YES → Same reservation
    │       │       │   ├── Times changed? → Update times, return (S, False, True)
    │       │       │   └── Times same? → No-op, return (S, False, False)
    │       │       └── NO (different UIDs) → Distinct reservations
    │       │           └── Fall through to "Not found" path
    │       │
    │       └── Not found (no name+overlap match)
    │           ├── _next_slot is not None?
    │           │   ├── YES → Write to _next_slot, recalculate next
    │           │   │         return (new_slot, True, False)
    │           │   └── NO → All slots occupied
    │           │            Log overflow (FR-010)
    │           │            return (None, False, False)
    │           │
    │           └── (dedup check in async_update also catches edge cases)
    │
    └── Lock released
```
