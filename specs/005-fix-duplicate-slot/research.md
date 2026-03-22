<!--
SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Research: Fix Duplicate Keymaster Code Slot Assignment

## R-001: asyncio.Lock Placement and Granularity

**Context**: FR-002 requires serializing all slot read-modify-write operations.
The codebase has a single `EventOverrides` instance per coordinator, and all slot
mutations flow through it.

**Decision**: Single `asyncio.Lock` owned by `EventOverrides.__init__()`.

**Rationale**:
- The HA event loop is single-threaded; contention arises only from interleaved
  coroutines at `await` points (e.g., service calls to Keymaster).
- A single lock per `EventOverrides` instance is sufficient because there is
  exactly one instance per config entry (one calendar → one coordinator → one
  `EventOverrides`).
- Finer-grained per-slot locks would add complexity without benefit — the
  critical section is the dict-level invariant check (no duplicate name+overlap),
  not individual slot writes.
- Lock scope: acquired before reading `_overrides` state, released after write
  completes and `__assign_next_slot()` recalculates.

**Alternatives considered**:
- **Per-slot locks**: Rejected — the dedup check must inspect all slots
  atomically, so per-slot locks would require acquiring all locks simultaneously
  (equivalent to a single lock but more complex).
- **threading.Lock**: Rejected — Home Assistant's event loop is asyncio-based;
  using a threading lock would block the event loop during `await` points inside
  the critical section.
- **asyncio.Semaphore(1)**: Functionally equivalent to Lock but less
  semantically clear; Lock is the standard choice.

---

## R-002: Converting sync update() to async

**Context**: `EventOverrides.update()` is currently synchronous (called from
`@callback` methods and async methods). Adding `asyncio.Lock` requires `await`,
so callers must be adapted.

**Decision**: Create new `async_update()` and `async_reserve_or_get_slot()`
methods; keep sync `update()` for bootstrap-only paths (where no contention
exists).

**Rationale**:
- During `async_setup_keymaster_overrides()`, slots are populated sequentially
  before any listeners are registered — no contention possible. The sync
  `update()` can remain for this path (no lock needed during bootstrap).
- After bootstrap, all mutation paths must use the async variants:
  - `_handle_coordinator_update()` → schedules
    `async_reserve_or_get_slot()` via `async_create_task()`
  - `async_check_overrides()` → calls `async_update()` (already async)
  - `update_event_overrides()` → calls `async_update()` (already async)
  - State change listener → calls `update_event_overrides()` (already async)
- The sync `update()` is retained but marked with a docstring warning that it
  must only be used during bootstrap when no concurrent access is possible.

**Alternatives considered**:
- **Remove sync update() entirely**: Rejected — would require making the
  bootstrap path async with lock acquisition for no benefit (no contention
  during bootstrap).
- **Make @callback methods async**: Rejected — HA's `@callback` decorator
  requires synchronous methods. The existing pattern of scheduling tasks via
  `async_create_task()` is the correct HA pattern.

---

## R-003: Identity Model — Name + Time-Range Overlap

**Context**: FR-001 defines the identity model: slot_name + overlapping time
range = same reservation. UID is a runtime-only tiebreaker.

**Decision**: Implement `_find_overlapping_slot()` method that scans
`_overrides` for any slot where `slot_name` matches AND time ranges overlap.

**Rationale**:
- Keymaster persists only `slot_name`, `slot_code`, `start_time`, `end_time` per
  slot — no UID storage. After HA restart, only name + time range survives.
- Two events with the same name but non-overlapping times are genuinely
  different reservations (e.g., repeat guest with back-to-back bookings).
- Time-range overlap is defined as: `start_a < end_b AND start_b < end_a`
  (standard interval overlap check).
- UID tiebreaker: During runtime, `CalendarEvent.uid` is available. If two
  events have the same name + overlapping times but different UIDs, they are
  distinct (e.g., same guest booked two overlapping units). This is checked only
  when both UIDs are non-None.

**Alternatives considered**:
- **UID-primary identity**: Rejected — UIDs are lost on restart; would cause
  slot duplication after every HA reboot.
- **Name-only identity**: Rejected — would prevent legitimate back-to-back
  stays by the same guest from getting separate slots.

---

## R-004: Atomic Reserve-or-Get Pattern

**Context**: FR-003 requires an atomic operation that either finds an existing
slot for a guest or reserves a new one.

**Decision**: `async_reserve_or_get_slot(slot_name, slot_code, start_time,
end_time, uid=None)` → returns `(slot_number, is_new)`.

**Rationale**:
- Acquires `_lock`, then:
  1. Calls `_find_overlapping_slot(slot_name, start_time, end_time, uid)`.
  2. If found: updates times if changed, returns `(existing_slot, False)`.
  3. If not found and `_next_slot is not None`: writes to `_next_slot`,
     recalculates next, returns `(new_slot, True)`.
  4. If not found and no slot available: logs overflow (FR-010), returns
     `(None, False)`.
- Releases `_lock`.
- The caller (`calsensor.py`) receives the slot number and only proceeds to
  Keymaster service calls if `is_new` is True (or if times were updated).
- This eliminates the check-then-act race: no window between checking
  `next_slot` and writing to it.

**Alternatives considered**:
- **Two-phase reserve then confirm**: Rejected — adds complexity of
  rollback/timeout without benefit in a single-process async environment.
- **Optimistic concurrency (check-then-retry)**: Rejected — the lock is
  cheap (microsecond in-memory); optimistic patterns add retry complexity.

---

## R-005: Dedup Rejection at Storage Layer

**Context**: FR-004 requires the storage layer to reject writes that would
create duplicate name+overlap entries (defense-in-depth).

**Decision**: Enforce the invariant inside `async_update()` — before writing,
scan all slots for duplicate name+overlap. If found and UIDs don't prove
distinctness, redirect to the existing slot and log a warning.

**Rationale**:
- This is the last line of defense. Even if `async_reserve_or_get_slot()` has a
  bug, `async_update()` catches it.
- Warning log includes: duplicate slot name, attempted target slot, existing
  slot, time ranges — sufficient for operational troubleshooting (FR-009).
- The redirect (update existing slot's times instead of creating a duplicate) is
  more useful than a hard rejection that would leave the event unassigned.

**Alternatives considered**:
- **Hard rejection (raise exception)**: Rejected — would leave the guest without
  a code slot. Redirect to existing slot is safer operationally.
- **No storage-layer check (rely on reservation only)**: Rejected — violates
  defense-in-depth principle (FR-004).

---

## R-006: Pre-Execution Slot Verification

**Context**: FR-007 requires verifying slot ownership before executing lock
commands. There's a time gap between reservation and execution (service calls are
async).

**Decision**: Add ownership verification at the start of `async_fire_set_code()`,
`async_fire_clear_code()`, and `async_fire_update_times()`.

**Rationale**:
- Between scheduling and execution, the slot may have been reassigned (e.g., by
  `async_check_overrides()` clearing an expired slot, then a new reservation
  taking it).
- Verification pattern: read `_overrides[slot]` and confirm `slot_name` matches
  the expected guest name. If mismatch, log warning and abort.
- This does NOT acquire the lock — it's a read-only check. The lock is for
  write serialization. A stale read here is acceptable because:
  - If the slot was reassigned, aborting is correct (the new owner will issue
    its own commands).
  - If the slot is still ours, proceeding is correct.
  - The worst case of a stale read is an unnecessary abort, which is safe (the
    coordinator cycle will retry).

**Alternatives considered**:
- **Acquire lock during entire service call execution**: Rejected — service
  calls to Keymaster involve multiple awaits (disable → configure → enable)
  that can take seconds. Holding the lock during this time would block all
  other slot operations, defeating the purpose of async.
- **Generation counter**: Considered but unnecessary — name comparison is
  sufficient because names are unique per slot (enforced by dedup).

---

## R-007: Retry and Escalation Strategy

**Context**: FR-011 and FR-012 require retry on failed set-code/clear-code with
escalation after 3 consecutive failures.

**Decision**: Track `_retry_counts: dict[int, int]` on `EventOverrides`, keyed
by slot number. Increment on failure, reset on success. Escalate via
`persistent_notification.async_create()` after threshold.

**Rationale**:
- The coordinator refresh cycle (default 2 min) provides natural retry
  opportunities — no dedicated retry timer needed.
- On each coordinator update, `_handle_coordinator_update()` re-evaluates the
  slot. If the slot is reserved but the lock command hasn't succeeded, it will
  re-trigger the command.
- `_retry_counts` persists in memory for the lifetime of the EventOverrides
  instance. On HA restart, counts reset to 0 (acceptable — if the slot is still
  in a bad state, retries resume from the new session).
- Persistent notification uses `notification_id` based on slot number to avoid
  duplicate notifications for the same slot.
- Escalation is informational — retries continue indefinitely.

**Alternatives considered**:
- **Exponential backoff timer**: Rejected — coordinator refresh already provides
  regular retry intervals; adding separate timers complicates lifecycle.
- **Release slot after N failures**: Rejected — releasing a slot with a
  potentially stale code on the lock is a security risk. The slot must stay
  occupied until confirmed.
- **HA repair flow**: Rejected — persistent_notification is simpler and
  sufficient for property managers.

---

## R-008: @callback to Async Adaptation in calsensor.py

**Context**: `_handle_coordinator_update()` is `@callback` (sync). It currently
reads `next_slot` and schedules `async_create_task(async_fire_set_code(...))`.
The new atomic reserve must be awaited.

**Decision**: Replace the `next_slot` check + `async_fire_set_code()` scheduling
with a single `async_create_task()` that wraps a new
`_async_handle_slot_assignment()` coroutine.

**Rationale**:
- The `@callback` decorator cannot be removed (HA framework requirement for
  coordinator entity updates).
- The new coroutine `_async_handle_slot_assignment()` will:
  1. Call `await overrides.async_reserve_or_get_slot(...)`.
  2. If new reservation: call `await async_fire_set_code(...)`.
  3. If existing with time update: call
     `await async_fire_update_times(...)`.
  4. If no-op: return silently.
- This moves all slot-mutating logic into a single async coroutine, eliminating
  the check-then-act race entirely.
- The `@callback` method becomes a pure "detect and dispatch" method — it
  extracts event data and schedules the async handler.

**Alternatives considered**:
- **Convert _handle_coordinator_update to async**: Rejected — HA's
  `CoordinatorEntity._handle_coordinator_update()` is `@callback`; overriding
  it as async would break the framework contract.
- **Use hass.loop.call_soon()**: Rejected — `async_create_task()` is the
  standard HA pattern for scheduling coroutines from `@callback` methods.

---

## R-009: async_check_overrides Lock Interaction

**Context**: `async_check_overrides()` iterates slots and may clear them. It
must not race with concurrent slot assignments.

**Decision**: Acquire the lock for the entire check-and-clear cycle within
`async_check_overrides()`.

**Rationale**:
- The method reads all slots, evaluates each against the calendar, and clears
  invalid ones. If another coroutine modifies slots between the read and the
  clear, the check is based on stale data.
- Holding the lock for the full iteration is acceptable because:
  - The iteration is fast (in-memory dict scan, O(max_slots) where max_slots
    is typically 5).
  - The only `await` within the critical section is `async_fire_clear_code()`,
    which is a single HA service call. This call must complete before the slot
    is marked as cleared to maintain consistency.
  - While the lock is held, concurrent `async_reserve_or_get_slot()` calls
    will queue (not block the event loop — `asyncio.Lock.acquire()` suspends
    the coroutine and allows other event loop work).
- Alternative: acquire/release per slot. Rejected because an assignment between
  iterations could create inconsistency (e.g., clearing a slot that was just
  assigned).

**Alternatives considered**:
- **Snapshot-then-clear pattern**: Take snapshot of slots to clear, release
  lock, then clear. Rejected — a new assignment to a "to-be-cleared" slot
  would be overwritten by the deferred clear.
- **No lock in check_overrides**: Rejected — FR-008 explicitly requires lock
  acquisition for the check-and-clear cycle.
