<!--
SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Feature Specification: Fix Duplicate Keymaster Code Slot Assignment

**Feature Branch**: `005-fix-duplicate-slot`
**Created**: 2025-07-17
**Status**: Draft
**Input**: User description: "Fix duplicate keymaster code slot assignment in the rental-control integration"

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Concurrent Reservations Get Unique Slots (Priority: P1)

A property manager has multiple reservations starting on overlapping days. When the system processes these reservations simultaneously (e.g., after a calendar refresh), each guest is assigned to exactly one code slot within the managed range. No guest appears in more than one slot, and no slot is inadvertently shared.

**Why this priority**: This is the core defect. Duplicate slot assignment is a security issue — it causes lock codes to persist after checkout or be overwritten unpredictably. Fixing this eliminates the root cause.

**Independent Test**: Can be fully tested by triggering a calendar refresh containing two or more new reservations and verifying that each guest name appears in exactly one slot across the entire managed range.

**Acceptance Scenarios**:

1. **Given** two new reservations ("Alice" and "Bob") arrive in the same calendar refresh, **When** the system processes them concurrently, **Then** "Alice" is assigned to exactly one slot and "Bob" is assigned to exactly one different slot.
2. **Given** three new reservations arrive simultaneously and only two managed slots are available, **When** the system processes them, **Then** exactly two guests receive slots and the third is handled gracefully (e.g., queued or logged as unassigned) without any guest receiving multiple slots.
3. **Given** a reservation for "Alice" already occupies slot 3, **When** a calendar refresh delivers the same "Alice" reservation again, **Then** the system recognizes the existing assignment and does not create a second slot for "Alice."

---

### User Story 2 — Idempotent Reservation Updates (Priority: P2)

A property manager's calendar source re-delivers a reservation with updated check-in or check-out times (but the same guest). The system updates the existing slot's time range rather than creating a duplicate entry. The correct code remains active only for the updated time window.

**Why this priority**: Calendar platforms frequently re-sync reservations with minor time adjustments. Without idempotent handling, each re-sync risks creating duplicates, compounding the core defect over time.

**Independent Test**: Can be fully tested by assigning a guest to a slot, then sending an updated reservation for the same guest with different times, and verifying the original slot's times are updated with no new slot created.

**Acceptance Scenarios**:

1. **Given** "Alice" is assigned to slot 3 with check-in Monday and check-out Friday, **When** the calendar delivers "Alice" again with check-out changed to Saturday, **Then** slot 3's time range is updated to Monday–Saturday and no additional slot is created.
2. **Given** "Alice" is assigned to slot 3, **When** the calendar delivers "Alice" with identical times, **Then** no changes are made (fully idempotent) and no duplicate slot is created.

---

### User Story 3 — Slot Cleanup After Checkout (Priority: P3)

When a reservation ends and the system clears a guest's code slot, it reliably clears all data associated with that guest. No orphaned slot data remains that could cause a stale code to linger on the lock.

**Why this priority**: Even with deduplication in place, the cleanup path must be safe against concurrent modifications. If cleanup races with new assignments, stale codes could persist on locks — a direct security concern.

**Independent Test**: Can be fully tested by assigning a guest to a slot, advancing time past checkout, and verifying the slot is fully cleared with no residual data.

**Acceptance Scenarios**:

1. **Given** "Alice" occupies slot 3 and her checkout time has passed, **When** the system runs its override check, **Then** slot 3 is fully cleared (name, code, times) and the code-clear command is sent to the lock.
2. **Given** "Alice" is being cleared from slot 3 at the same moment "Bob" is being assigned to slot 4, **When** both operations execute concurrently, **Then** each operation completes correctly — slot 3 is cleared and slot 4 is assigned — with no cross-contamination.
3. **Given** "Alice" occupies slot 3 and a cleanup begins, **When** a concurrent calendar refresh re-delivers "Alice" with a future reservation, **Then** the cleanup completes for the expired reservation, and the new reservation is assigned cleanly (to slot 3 or another available slot).

---

### User Story 4 — Duplicate Prevention as Last Line of Defense (Priority: P4)

Even if upstream logic fails to prevent a duplicate assignment attempt, the slot storage layer rejects any write that would place the same guest name with an overlapping time range into more than one slot. This acts as a safety net regardless of how the duplicate request originated.

**Why this priority**: Defense-in-depth. Even with concurrency controls, bugs in future code changes or unexpected event ordering could attempt a duplicate write. A hard constraint at the storage layer guarantees the invariant is never violated.

**Independent Test**: Can be fully tested by directly invoking the slot update operation with a guest name and overlapping time range that already exists in a different slot, and verifying the operation either redirects to the existing slot or rejects the write with a logged warning.

**Acceptance Scenarios**:

1. **Given** "Alice" is assigned to slot 3 with times Monday–Friday, **When** a slot update is attempted that would place "Alice" into slot 5 with overlapping times (e.g., Wednesday–Sunday), **Then** the update is rejected, slot 3's times are updated to Monday–Sunday, slot 5 is not modified, and a warning is logged.
2. **Given** "Alice" is assigned to slot 3 with times Monday–Friday, **When** a slot update attempts to place "Alice" into slot 5 with times Monday–Saturday, **Then** slot 3's times are updated to Monday–Saturday, slot 5 is not modified, and a warning is logged.
3. **Given** "Alice" is assigned to slot 3 with times Monday–Friday, **When** a slot update attempts to place "Alice" into slot 5 with non-overlapping times (e.g., the following Monday–Friday), **Then** "Alice" is assigned to slot 5 as a separate reservation, both slots remain active, and no warning is logged (this is a legitimate back-to-back stay).

---

### Edge Cases

- What happens when all managed slots are occupied and a new reservation arrives? The system must not overwrite an existing active reservation and should log the overflow condition.
- What happens when a guest name changes between calendar fetches (e.g., "Alice Smith" becomes "Alice S.")? The system treats these as different guests since the name component of the identity no longer matches. UID could disambiguate at runtime, but after restart UIDs are lost. This is a known limitation documented in Non-Goals.
- What happens when the lock integration (Keymaster) is temporarily unavailable during a set-code or clear-code operation? For set-code failures, the system retries on subsequent coordinator cycles and escalates after 3 failures per FR-011. For clear-code failures, the same retry-and-escalate pattern applies per FR-012 — the slot remains occupied until clear is confirmed, preventing premature slot reuse and stale lock codes.
- What happens when a slot reservation is made but the subsequent set-code operation fails? The reserved slot remains reserved and the set-code command is retried on subsequent coordinator update cycles. The system tracks consecutive failures per slot; after 3 consecutive failed cycles (configurable, default 3), it escalates by creating a persistent notification to alert the property manager while continuing retries. The existing coordinator refresh provides natural retry opportunities with no dedicated retry infrastructure needed. The slot must not be released until either the code is successfully set or the reservation expires.
- What happens when two reservations for genuinely different guests have identical names? Same-name reservations are distinguished by non-overlapping time ranges — each receives a separate slot. If both name AND time ranges overlap, CalendarEvent UID disambiguates during runtime; after a restart (when UIDs are lost), they are treated as the same reservation. This is a known limitation (see next edge case).
- What happens when two reservations have the same guest name AND overlapping time ranges but are genuinely different reservations (e.g., the same guest booked two overlapping units)? During runtime, CalendarEvent UID distinguishes them and each receives a separate slot. After a Home Assistant restart, UIDs are lost (Keymaster does not persist them), so the reservations cannot be distinguished — they are treated as the same reservation and collapse to a single slot. This is a known limitation; resolving it would require Keymaster to store additional custom metadata per slot.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST use slot name (guest name) combined with time-range overlap as the primary identity for slot assignment. Two events with the same slot name and overlapping time ranges MUST be treated as the same reservation (update the existing slot). Two events with the same slot name but non-overlapping time ranges MUST be treated as different reservations (assign separate slots). CalendarEvent UID serves as a runtime-only tiebreaker: when two events share the same slot name and overlapping time range but have different UIDs during a single runtime session, they are treated as distinct reservations. UID is not persisted across Home Assistant restarts and MUST NOT be relied upon as the primary identity key.
- **FR-002**: System MUST serialize all slot read-modify-write operations using a single `asyncio.Lock` held by the EventOverrides component. All slot mutations (assign, update, clear) MUST acquire this lock before reading state and release it only after the write completes, so that concurrent calendar processing cannot create race conditions between checking slot availability and writing slot assignments.
- **FR-003**: System MUST provide an atomic "reserve or retrieve" async operation for slot assignment that acquires the EventOverrides lock, performs the existence check and slot reservation as a single indivisible step, and releases the lock afterward. Callers that were previously sync MUST be adapted to await this operation.
- **FR-004**: System MUST reject any slot write that would assign a guest name with an overlapping time range to a new slot when that guest name with an overlapping time range already exists in a different slot, and MUST log a warning when such a duplicate is detected and prevented. During runtime, if CalendarEvent UIDs are available and differ, the write is permitted (the events are genuinely distinct despite name/time overlap).
- **FR-005**: System MUST handle reservation time updates idempotently — when a reservation is re-delivered with changed times for an already-assigned guest, the existing slot's time range MUST be updated rather than creating a new slot.
- **FR-006**: System MUST handle reservation re-delivery with identical data as a no-op — no slot modifications, no duplicate entries, no unnecessary lock commands.
- **FR-007**: System MUST re-verify slot ownership immediately before executing any lock command (set-code or clear-code) and abort the operation if the slot is no longer reserved for the expected guest.
- **FR-008**: System MUST ensure that the override check/cleanup process acquires the EventOverrides `asyncio.Lock` for the duration of its check-and-clear cycle, preventing concurrent modifications from creating inconsistent state. Since the existing `@callback`-decorated `_handle_coordinator_update()` is synchronous but schedules async tasks, any slot-mutating path it triggers MUST await the lock-protected async methods rather than calling sync mutation methods directly.
- **FR-009**: System MUST log all slot assignment, update, and rejection events at an appropriate detail level for operational troubleshooting.
- **FR-010**: System MUST gracefully handle the condition where all managed slots are occupied — new reservations must not overwrite active reservations, and the overflow must be logged.
- **FR-011**: When a set-code operation fails after a slot has been reserved, the system MUST keep the slot reserved and retry the set-code command on subsequent coordinator update cycles. The system MUST track consecutive failed retry attempts per slot. After 3 consecutive failed coordinator cycles (configurable, default 3), the system MUST escalate by creating a persistent notification via `persistent_notification.async_create()` to alert the property manager of the failure. Retries MUST continue even after escalation — the persistent notification is informational, not terminal. The slot MUST NOT be released until the code is successfully set or the reservation expires. No dedicated retry infrastructure is required; the existing coordinator refresh interval serves as the retry mechanism.
- **FR-012**: When a clear-code operation fails during slot cleanup after checkout, the system MUST keep the slot occupied (not release it) and retry the clear-code command on subsequent coordinator update cycles. The system MUST track consecutive failed clear-code attempts per slot. After 3 consecutive failed coordinator cycles (configurable, default 3), the system MUST escalate by creating a persistent notification via `persistent_notification.async_create()` to alert the property manager that a stale lock code may still be active on the physical lock. Retries MUST continue even after escalation. The slot MUST NOT be marked as available until the clear-code operation is confirmed successful, preventing premature slot reuse. This mirrors the set-code failure handling defined in FR-011.

### Key Entities

- **Code Slot**: A numbered position within the managed range of Keymaster code slots. Each slot holds a guest name, access code, start time, and end time. A slot is either empty or assigned to exactly one guest.
- **Event Override**: The mapping between a calendar reservation event and a code slot. Keyed by slot name (guest name) combined with time-range overlap: two entries with the same slot name and overlapping time ranges are considered the same reservation. CalendarEvent UID is retained as a runtime-only disambiguator when available but is not persisted across restarts. Contains the slot name, the assigned slot number, time range, and code. Stored and managed by the EventOverrides component.
- **Calendar Sensor**: A per-event sensor that monitors a single reservation from the calendar source. Responsible for detecting new, changed, or expired reservations and initiating slot operations.
- **Managed Range**: The contiguous range of Keymaster code slots that the rental-control integration is allowed to manage. Defined by configuration. The system must not read or write slots outside this range.

## Assumptions

- The Keymaster integration is the sole consumer of code slots within the managed range — no external system or manual process modifies slots in the managed range concurrently.
- Guest name derivation from calendar events (via `get_slot_name()`) is deterministic for a given reservation within a single calendar refresh cycle. Known edge cases with name changes across fetches are out of scope (see Non-Goals).
- The Home Assistant event loop is single-threaded (standard asyncio). Concurrency arises from interleaved coroutines at `await` points, not from true parallel threads. Functions decorated with `@callback` run synchronously within the event loop and are naturally atomic (no interleaving), but any async work they schedule (e.g., slot mutations) must still acquire the EventOverrides lock.
- Existing `event_overrides.update()` is currently synchronous. Converting slot mutation methods to async (to acquire/release `asyncio.Lock`) requires callers to be adapted — `@callback` functions must schedule these via `hass.async_create_task()` or equivalent rather than calling them directly.
- Existing Keymaster service calls (`set_usercode`, `clear_usercode`) are idempotent — calling them with the same parameters multiple times produces the same result.
- Keymaster does not persist custom metadata (such as CalendarEvent UIDs) per code slot. On Home Assistant restart, EventOverrides is reconstructed from Keymaster entity state, which stores only slot_name, code, start_time, and end_time. Therefore, the identity model must be fully reconstructable from slot_name and time ranges alone — UID is ephemeral and serves only as a runtime tiebreaker.

## Non-Goals

- Changing the `get_slot_name()` regex extraction logic that derives guest names from calendar events. Name-matching improvements are a separate concern.
- Modifying CalendarEvent UID tracking (already addressed in PR #410).
- Changes to the checkin tracking sensor (covered by the Phase 1-9 checkin-tracking feature).
- Handling the case where a guest's display name changes between calendar fetches (e.g., due to platform-specific formatting). This is a known limitation.

## Clarifications

### Session 2026-03-21

- Q: What should the primary deduplication key be for slot assignment? → A: ~~UID preferred, guest-name fallback (Option C)~~ **Revised**: Name + time-range overlap as primary identity, UID as runtime-only tiebreaker. Rationale: Keymaster does not persist CalendarEvent UIDs; on HA restart, UIDs are lost and cannot be used to reconcile slot mappings. The stable identity that survives the persistence boundary is slot_name + overlapping time range.
- Q: What concurrency control mechanism should serialize slot mutations? → A: Single `asyncio.Lock` on EventOverrides — all slot mutations serialized at the data layer (Option A).
- Q: When a set-code operation fails after slot reservation, what recovery strategy should the system use? → A: Retry on next coordinator update — keep slot reserved, retry lock command on next refresh cycle, no new retry infrastructure (Option A).
- Q: When set-code retries keep failing across coordinator cycles, when should the system escalate? → A: Retry for 3 coordinator cycles, then escalate to persistent notification via `persistent_notification.async_create()` while continuing retries (Option C).
- Q: When a clear-code operation fails during slot cleanup, what recovery strategy should the system use? → A: Same pattern as set-code (FR-011): retry for 3 coordinator cycles, then escalate to persistent notification. Slot stays occupied until clear confirmed (Option A).

## Security Considerations

- **Physical Access Impact**: Duplicate code slots directly affect physical lock access codes. A duplicate can cause codes to persist after checkout (one slot cleared, duplicate remains active) or cause wrong codes to be assigned to locks.
- **Defense in Depth**: The multi-layer approach (serialization + dedup rejection + atomic reservation + pre-execution verification) ensures that no single bug can result in duplicate physical access codes.
- **Audit Trail**: All slot operations (assign, update, reject, clear) must be logged to support post-incident investigation of any access anomalies.
- **Stale Code Mitigation**: Failed clear-code operations are handled by the same retry-and-escalate pattern as set-code failures (FR-012). The slot remains occupied until the clear is confirmed, preventing the slot from being reassigned while a stale code may still be active on the physical lock. Persistent notification escalation ensures the property manager is alerted to intervene if automated retries cannot resolve the issue.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Under concurrent processing of 10 simultaneous new reservations, each guest is assigned to exactly one code slot with zero duplicates, 100% of the time across repeated test runs.
- **SC-002**: Re-delivery of an existing reservation with updated times results in the original slot being updated (not a new slot created) in 100% of cases.
- **SC-003**: Re-delivery of an identical reservation produces zero slot modifications and zero lock commands.
- **SC-004**: When all managed slots are occupied, new reservation arrivals result in zero overwrites of active reservations and a logged overflow event.
- **SC-005**: Slot cleanup after checkout completes fully (slot cleared, lock command sent) even when concurrent assignments are in progress, with zero cross-contamination between operations.
- **SC-006**: Any attempt to write a guest name with an overlapping time range to a slot when that name + overlapping time range already exists in a different slot is rejected and logged, with zero exceptions across all code paths (except when distinct CalendarEvent UIDs are available at runtime to prove the events are genuinely different).
- **SC-007**: Existing single-reservation workflows (assign, update times, clear after checkout) continue to function correctly with no user-visible behavior changes.
