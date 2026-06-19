<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Data Model: Slot Reconciliation

## Entities

### Reservation

A normalized calendar stay eligible for slot planning.

| Field | Type | Purpose |
|-------|------|---------|
| `identity_key` | `str` | Versioned stable fingerprint of normalized slot name, start, end, and entry scope. |
| `uid_aliases` | `set[str]` | Volatile iCal UIDs seen for this reservation; aliases only, not primary identity. |
| `booking_aliases` | `set[str]` | Optional extracted booking or confirmation identifiers when available. |
| `fingerprint_history` | `set[str]` | Prior stable fingerprints for conservative rematch after date or UID changes. |
| `summary` | `str` | Calendar summary for sensor display. |
| `slot_name` | `str` | Unprefixed, untrimmed guest-facing slot name from existing extraction logic. |
| `display_slot_name` | `str` | Prefixed/trimmed Keymaster name computed at write time. |
| `slot_code` | `str` | Generated or retained code for current planning; not persisted raw in Store. |
| `start` | `datetime` | Reservation check-in/access start before lock-code buffer. |
| `end` | `datetime` | Reservation checkout/access end before lock-code buffer. |
| `buffered_start` | `datetime` | Keymaster date-range start after existing before-buffer. |
| `buffered_end` | `datetime` | Keymaster date-range end after existing after-buffer. |
| `eligible` | `bool` | True when current parser/config rules include the reservation. |
| `protected_active` | `bool` | True for a currently checked-in guest inside the active stay window. |
| `checked_out` | `bool` | True when check-in tracking says the stay has checked out. |
| `missing_count` | `int` | Consecutive refreshes missing from feed while persisted and assigned. |
| `desired_slot` | `int | None` | Slot selected by the current desired plan. |
| `overflow_reason` | `str | None` | Why it is not assigned, e.g. `capacity` or `blocked_clear`. |

**Validation rules**:

- One `identity_key` maps to at most one managed slot.
- `start < end` after timezone normalization.
- `protected_active` reservations count against managed-slot capacity.
- A reservation missing from the feed remains eligible through miss counts 1 and
  2; miss count 3 makes it clearable unless protected.

### ManagedSlot

A numbered Keymaster slot inside the RC-managed range.

| Field | Type | Purpose |
|-------|------|---------|
| `slot` | `int` | Physical Keymaster slot number. |
| `managed` | `bool` | True only inside configured `start_slot .. start_slot + max_events - 1`. |
| `actual_name` | `str | None` | Observed Keymaster slot name, normalized for unknown/unavailable. |
| `actual_code_present` | `bool | None` | Whether the PIN text entity contains a usable code. `None` means unknown. |
| `actual_start` | `datetime | None` | Observed Keymaster date-range start. |
| `actual_end` | `datetime | None` | Observed Keymaster date-range end. |
| `date_range_enabled` | `bool | None` | Observed Keymaster use-date-range switch state. |
| `enabled` | `bool | None` | Observed Keymaster slot enabled switch state. |
| `desired_identity_key` | `str | None` | Desired reservation for this slot in the current plan. |
| `persisted_identity_key` | `str | None` | Previously stored reservation for this slot. |
| `status` | `SlotStatus` | `free`, `occupied`, `pending_clear`, `blocked`, `phantom`, or `unknown`. |
| `blocked_reason` | `str | None` | Reason the slot cannot be reused. |
| `retry_count` | `int` | Consecutive failed physical operations for this slot. |
| `last_operation_id` | `str | None` | Reconcile operation token used to classify callback echoes. |
| `dirty_during_operation` | `bool` | True when a callback observed state while an operation token was pending. |

**Validation rules**:

- Unmanaged slots are never modified.
- A slot with `status in {'pending_clear', 'blocked', 'unknown'}` cannot receive
  a different reservation.
- A slot becomes `free` only after actual name and PIN are empty or unavailable,
  date-range limits are off or reset, and enabled state is off or consistent
  with Keymaster reset semantics.

### DesiredPlan

The deterministic refresh result.

| Field | Type | Purpose |
|-------|------|---------|
| `plan_id` | `str` | Refresh-scoped identifier for logging and operation tokens. |
| `generated_at` | `datetime` | Time the plan was computed. |
| `selected` | `dict[str, int]` | Reservation identity to desired slot. |
| `protected` | `set[str]` | Selected identities protected by checked-in state. |
| `overflow` | `dict[str, str]` | Unassigned eligible identities and reasons. |
| `slots` | `dict[int, PlannedSlot]` | Desired and actual comparison per managed slot. |
| `actions` | `list[SlotAction]` | Ordered diff to apply. |
| `diagnostics` | `dict[str, Any]` | Desired-vs-actual capture for support. |

**Validation rules**:

- `len(selected) <= max_events` unless protected active reservations exceed
  capacity, in which case all protected reservations remain assigned and the
  capacity violation is diagnostic-only.
- Each selected identity appears once.
- Each selected slot appears once.
- No selected reservation is assigned behind a farther unprotected reservation
  once physical operations are confirmed.

### Persisted SlotMapping

Home Assistant Store record that survives restarts.

| Field | Type | Purpose |
|-------|------|---------|
| `schema_version` | `int` | Store schema version. Starts at `1`. |
| `entry_id` | `str` | Config entry scope. |
| `lockname` | `str | None` | Keymaster lock scope for migration checks. |
| `start_slot` | `int` | Managed range start. |
| `max_slots` | `int` | Managed range length. |
| `identity_key` | `str` | Primary Reservation identity. |
| `slot` | `int` | Persisted desired/last confirmed slot. |
| `status` | `str` | `occupied`, `pending_set`, `pending_clear`, `blocked`, or `overflow`. |
| `identity` | `StoredIdentity` | Stable fields and aliases. |
| `missing_count` | `int` | Consecutive feed misses. |
| `operation_id` | `str | None` | Persisted fence token for an in-flight set or clear. |
| `operation_kind` | `str | None` | `set` or `clear` while an operation is pending. |
| `pending_set_since` | `datetime | None` | When a set became in-flight and not yet verified. |
| `pending_clear_since` | `datetime | None` | When clear became unconfirmed. |
| `last_observed_actual` | `StoredActual` | Redacted last actual state for diagnostics/migration. |
| `fingerprint_history` | `list[str]` | Prior fingerprints that may identify the same reservation after date shifts. |
| `updated_at` | `datetime` | Last Store update time. |

**Store invariants**:

- No two mappings may claim the same slot unless at most one is `occupied` and
  the other is historical/overflow diagnostic state.
- `pending_set` and `pending_clear` slots are fenced after restart until a
  refresh verifies the physical state and either completes or blocks the
  operation.
- Raw PIN values are not stored. Store may persist `has_code` and a redacted hash
  only for drift detection.

### ActualKeymasterState

Refresh-time observation of managed Keymaster entities.

| Field | Type | Purpose |
|-------|------|---------|
| `slot` | `int` | Slot number. |
| `name_state` | `str | None` | Raw text entity state after unknown/unavailable normalization. |
| `pin_state` | `str | None` | Raw PIN only in memory for immediate comparison; redacted in logs. |
| `start_state` | `datetime | None` | Parsed date-range start. |
| `end_state` | `datetime | None` | Parsed date-range end. |
| `use_date_range` | `bool | None` | Date-range switch. |
| `enabled` | `bool | None` | Slot enabled switch. |
| `classification` | `str` | `free`, `occupied`, `phantom`, `partial_reset`, or `unknown`. |

## Relationships

```text
RentalControlCoordinator (1)
    │
    ├── Store-backed SlotMapping collection (0..max_events)
    │       └── keyed by Reservation.identity_key
    │
    ├── EventOverrides (1)
    │       ├── asyncio.Lock shared by reconciliation and callbacks
    │       ├── actual ManagedSlot cache
    │       ├── pending-clear/operation state
    │       └── diagnostics snapshot
    │
    ├── DesiredPlan (0..1 latest)
    │       ├── selected Reservation -> slot mappings
    │       ├── overflow reservations
    │       └── SlotAction apply-diff
    │
    └── RentalControlCalSensor (max_events)
            └── read-only display of Reservation plus assigned slot from DesiredPlan
```

## State Transitions

### Managed Slot Lifecycle

```text
                     ┌────────────┐
                     │    FREE    │
                     └─────┬──────┘
                           │ set desired reservation, verify physical state
                           ▼
                     ┌────────────┐
                     │  OCCUPIED  │
                     └──┬─────┬───┘
        manual drift │     │ reservation no longer desired/duplicate/expired
                     │     ▼
                     │ ┌───────────────┐
                     │ │ PENDING_CLEAR │
                     │ └──┬─────────┬──┘
                     │    │         │ clear failed or unknown
                     │    │         ▼
                     │    │   ┌─────────┐
                     │    │   │ BLOCKED │ retry/report, never assign new guest
                     │    │   └────┬────┘
                     │    │        │ later confirmed clear
                     │    ▼        │
                     └► RECONCILE ◄┘
                           │ confirmed actual empty
                           ▼
                     ┌────────────┐
                     │    FREE    │
                     └────────────┘
```

### Feed-Miss Lifecycle

```text
present in feed ──► missing_count = 0
      │
      ├─ absent once  ──► missing_count = 1, keep slot
      ├─ absent twice ──► missing_count = 2, keep slot
      ├─ absent third ─► clearable by normal desired-plan rules
      └─ reappears before third miss ─► missing_count = 0, keep mapping
```

### Duplicate Collapse

```text
duplicate actual slots for same identity
      │
      ├─ choose canonical slot:
      │    protected slot first, then persisted desired slot, then lowest slot
      │
      ├─ canonical remains occupied or is corrected in place
      │
      └─ non-canonical duplicates transition to pending_clear and cannot be
         reused until physical clear confirmation
```

### Manual Drift Correction

```text
actual managed slot differs from desired mapping
      │
      ├─ if operation token matches current reconcile: classify as callback echo
      │
      ├─ else log manual/external overwrite with redacted fields
      │
      └─ apply desired set/update/clear and verify actual convergence
```

### Operation Token Transaction

```text
acquire EventOverrides lock
      │
      ├─ compute diff and mark slot pending_clear/pending_set with token
      ├─ persist Store fence before service call
      └─ release lock
              │
              ▼
       call Keymaster service and read physical state directly
              │
              ▼
      reacquire EventOverrides lock
              │
              ├─ token still current and verification passed ─► free/occupied
              ├─ token still current and verification failed ─► blocked
              └─ token changed ─► leave newer state intact and report stale op
```

State-change callbacks that fire while a token is pending may set
`dirty_during_operation` or refresh the observed-state cache under the same lock,
but they do not clear the fence or start reconciliation.

## SlotAction Types

| Action | Preconditions | Postcondition |
|--------|---------------|---------------|
| `noop` | Actual already matches desired. | Mapping remains confirmed. |
| `set` | Slot is confirmed free and desired reservation exists. | Slot becomes occupied after post-set verification. |
| `update_times` | Same reservation/code, changed buffered dates. | Actual dates match desired or action retries later. |
| `clear` | Slot contains undesired, duplicate, stale, or phantom state. | Slot becomes pending-clear, then free only after confirmation. |
| `retry_clear` | Slot is pending-clear or blocked. | Retry count/logs update; slot remains unavailable until confirmed. |
| `overwrite_manual_change` | Actual drift conflicts with desired. | Desired state is restored and logged. |
| `blocked` | Actual state unknown or clear unconfirmed. | No new reservation is assigned to the slot. |

## Diagnostics Snapshot

Diagnostics should expose one capture per refresh with:

- `plan_id`, timestamp, entry ID, lockname, start slot, max slots.
- Per-slot desired reservation identity, actual Keymaster classification,
  planned action, pending/blocked reason, retry count, and last error.
- Per-reservation selected/protected/overflow status, missing count, assigned
  slot, and identity aliases.
- Manual drift events and duplicate collapse decisions with redacted PIN data.
