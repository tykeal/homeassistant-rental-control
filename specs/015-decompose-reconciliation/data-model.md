<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Data Model: Decompose Reconciliation Engine

## Compatibility Export Set

The package root `custom_components.rental_control.reconciliation` remains the
only public compatibility boundary. It re-exports:

| Symbol | Owner module | Preservation rule |
|--------|--------------|-------------------|
| `FINGERPRINT_VERSION` | `enums.py` | Keep value `"v1"`. |
| `SlotStatus` | `enums.py` | Keep enum values `free`, `occupied`, `pending_clear`, `blocked`, `phantom`, `unknown`. |
| `ObservedSlotStatus` | `enums.py` | Keep enum values `empty`, `occupied`, `phantom`, `unknown`. |
| `ActionKind` | `enums.py` | Keep all current legacy and stateless action values. |
| `SlotAction` | `action_models.py` | Keep fields, defaults, and action metadata semantics. |
| `Reservation` | `plan_models.py` | Keep constructor fields, validation, and mutable planning fields. |
| `ManagedSlot` | `plan_models.py` | Keep observed/persisted slot fields and defaults. |
| `ObservedSlot` | `stateless_models.py` | Keep physical classification derivation and `raw_pin` redaction. |
| `DesiredReservation` | `stateless_models.py` | Keep stateless desired fields and normalized-name derivation. |
| `StatelessPlan` | `stateless_models.py` | Keep selected, overflow, actions, diagnostics containers. |
| `CacheOnlyStoreRecord` | `store_models.py` | Keep cache-only metadata fields; never authoritative. |
| `PlannedSlot` | `plan_models.py` | Keep per-slot desired/actual/action comparison fields. |
| `DesiredPlan` | `plan_models.py` | Keep selected, protected, overflow, slots, actions, diagnostics, and validation. |
| `StoredIdentity` | `store_models.py` | Keep persisted identity fields and alias lists. |
| `StoredActual` | `store_models.py` | Keep redacted last-observed physical fields. |
| `SlotMapping` | `store_models.py` | Keep Store mapping fields and validation for compatibility. |
| `RematchKind` | `rematch_models.py` | Keep hierarchy classification values. |
| `RematchResult` | `rematch_models.py` | Keep result fields and ambiguity/date-shift semantics. |
| `normalize_slot_name_for_fingerprint` | `identity.py` | Keep strip/casefold normalization. |
| `make_reservation_fingerprint` | `identity.py` | Keep v1 canonical string and SHA-256 output. |
| `extract_booking_aliases` | `identity.py` | Keep Airbnb confirmation-code extraction. |
| `find_reservation_rematch` | `rematch.py` | Keep exact rule priority and return semantics. |
| `compute_desired_plan` | `desired.py` | Keep legacy caller call patterns and output identity. |
| `compute_stateless_plan` | `stateless.py` | Keep current caller signature and output identity. |

## Engine Entities

### Reservation

**Owner**: `plan_models.py`

A normalized calendar stay eligible for legacy desired-plan computation.

**Fields to preserve**: `identity_key`, `start`, `end`, `buffered_start`,
`buffered_end`, `summary`, `slot_name`, `display_slot_name`, `slot_code`,
`uid_aliases`, `booking_aliases`, `fingerprint_history`, `eligible`,
`protected_active`, `checked_out`, `missing_count`, `desired_slot`,
`overflow_reason`, `sensor_lookup_keys`, and `code_source`.

**Validation**: `start < end`; `missing_count >= 0`.

**Relationships**:

- Grouped by `identity.normalize_slot_name_for_fingerprint(slot_name)` for
  stable-name duplicate matching.
- Selected into `DesiredPlan.selected` by `desired.py`.
- Compared with `ManagedSlot` by `actions.py` for drift and date updates.
- Reconnected to Store cache entries by `rematch.py` without making Store data
  authoritative for correctness.

### ManagedSlot

**Owner**: `plan_models.py`

A Keymaster slot in or near the Rental-Control-managed range for legacy
`DesiredPlan` computation.

**Fields to preserve**: `slot`, `managed`, `status`, `actual_name`,
`actual_code`, `actual_code_present`, `actual_start`, `actual_end`,
`date_range_enabled`, `enabled`, `desired_identity_key`,
`persisted_identity_key`, `blocked_reason`, `preserve_unmatched`, `retry_count`,
`last_operation_id`, `dirty_during_operation`, and `last_error`.

**State rules**:

- `PENDING_CLEAR`, `BLOCKED`, and `UNKNOWN` do not receive different
  reservations.
- `FREE` slots are assignable by lowest slot number.
- `OCCUPIED` or `PHANTOM` slots participate in stable-name matching and may
  clear, retry, update in place, or no-op based on current fields.
- Duplicate non-canonical physical matches reset through the same clear path.

### ObservedSlot

**Owner**: `stateless_models.py`

A stateless physical slot fact read during one refresh.

**Fields to preserve**: `slot`, `managed`, `raw_name`, `raw_pin`, `has_pin`,
`actual_start`, `actual_end`, `date_range_enabled`, `enabled`, `readable`,
`empty_confirmed`, `classification`, `normalized_name_forms`, and
`matched_desired_id`.

**Validation/state derivation**:

- `raw_pin` remains memory-only and is excluded from diagnostics and Store data.
- Present blank, `unknown`, and `none` text values are cleared; `has_pin=False`
  is also confirmed clear. A missing PIN state where both `raw_pin is None` and
  `has_pin is None` remains unreadable/unknown, not confirmed empty.
- `unavailable` makes the slot unreadable and not confirmed empty.
- Managed readable slots classify as `EMPTY`, `OCCUPIED`, or `PHANTOM`; unmanaged
  or unreadable slots classify as `UNKNOWN`.

### DesiredReservation

**Owner**: `stateless_models.py`

A stateless planner reservation derived from current calendar and check-in state.

**Fields to preserve**: `desired_id`, `stable_slot_name`, `display_slot_name`,
`start`, `end`, `buffered_start`, `buffered_end`, `slot_code`, `code_source`,
`event_uid`, `booking_aliases`, `eligible`, `protected_active`, `checked_out`,
`selected_rank`, `matched_slot`, `assigned_slot`, `sensor_lookup_keys`,
`physical_time_override`, `overflow_reason`, and `normalized_name_forms`.

**Validation**: `start < end`; normalized forms include stable and display names.

**Relationships**:

- Grouped by stable name for stateless slot matching.
- Matched to `ObservedSlot` by `stateless.py` and `pairing.py`.
- Selected into `StatelessPlan.selected` at most once.

### DesiredPlan

**Owner**: `plan_models.py`; assembled by `desired.py`; diagnostics by
`diagnostics.py`.

**Fields to preserve**: `plan_id`, `generated_at`, `selected`, `protected`,
`overflow`, `slots`, `actions`, and `diagnostics`.

**Invariants**:

- Every selected identity appears at most once.
- Every selected slot appears at most once.
- `NOOP` actions are suppressed from `actions` but represented in `slots`.
- Diagnostics include the same plan metadata, slot entries, reservation entries,
  overflow details, stable-name matches, sorted aliases, drift fields, retry
  counts, and last errors as the current source.

### StatelessPlan

**Owner**: `stateless_models.py`; assembled by `stateless.py`; diagnostics by
`diagnostics.py`.

**Fields to preserve**: `plan_id`, `generated_at`, `observed_slots`,
`desired_reservations`, `selected`, `overflow`, `actions`, and `diagnostics`.

**Invariants**:

- Every selected `desired_id` appears in at most one slot.
- Unknown slots emit blocked actions.
- Duplicate non-canonical observed slots reset.
- New assignments require confirmed-empty physical slots.
- Update-in-place for code/name replacement stays bound to the matched physical
  slot and requires confirmed empty before reapply.

### SlotAction and PlannedSlot

**Owners**: `action_models.py` for `SlotAction` and `plan_models.py` for
`PlannedSlot`; populated by `actions.py`, `desired.py`, and `stateless.py`.

`SlotAction` carries executable action intent: `kind`, `slot`, `identity_key`,
`reason`, `desired_id`, `matched_by`, `requires_confirmed_empty`, `sequence`,
`preflight_read`, and `blocked_reason`.

`PlannedSlot` carries per-slot diagnostic comparison: `slot`,
`desired_identity_key`, `actual_classification`, `action`, `pending_reason`,
`retry_count`, and `last_error`.

**Action rules to preserve**:

- `SET`/`ASSIGN` only target confirmed-free or confirmed-empty slots.
- `OVERWRITE_MANUAL_CHANGE`/`UPDATE_IN_PLACE` keep stable-name matched
  reservations in their current physical slot.
- `CLEAR`/`RESET` handles stale, phantom, duplicate, and mis-assigned occupants.
- `RETRY_CLEAR` and `BLOCKED` preserve pending and unreadable safety behavior.
- `requires_confirmed_empty` and `preflight_read` remain set on replacement and
  assignment paths exactly as today.

### RematchResult and Store records

**Owners**: `store_models.py` and `rematch_models.py` for data; `rematch.py` for
behavior.

`StoredIdentity`, `StoredActual`, `SlotMapping`, and `CacheOnlyStoreRecord`
remain importable for compatibility and diagnostics. Stale Store snapshots must
not become authoritative for selection, duplicate prevention, reset decisions,
or assignment safety. The decomposition still preserves the current
`ManagedSlot.persisted_identity_key` continuity signal when the coordinator has
already resolved it for the refresh and fresh physical state does not contradict
it; current `compute_desired_plan` uses that field for same-slot matching and
protected pending-clear behavior.

`find_reservation_rematch()` returns `RematchResult` with:

1. `EXACT` for unchanged primary fingerprint unless fresh physical name
   contradicts the reservation;
2. `UID_ALIAS` plus name with `date_shifted=True`;
3. `BOOKING_ALIAS` plus name;
4. `NAME_TIME` for normalized name plus exact UTC start/end;
5. `CONTINUITY` for one conservative compatible candidate with no competing
   current reservation;
6. `CONTINUITY` for the one date-matching candidate when multiple continuity
   candidates exist but exactly one matches stored or observed dates;
7. `AMBIGUOUS` for unresolved competing candidates;
8. `NO_MATCH` otherwise.

## Internal Request Models

### DesiredPlanRequest

**Owner**: `desired.py`

Bundles the legacy `compute_desired_plan` inputs:

| Field | Purpose |
|-------|---------|
| `reservations` | Current `Reservation` records. |
| `managed_slots` | Current `ManagedSlot` observations. |
| `max_events` | Managed slot capacity. |
| `plan_id` | Refresh-scoped plan identifier. |
| `generated_at` | Plan timestamp. |
| `entry_id` | Optional diagnostics context. |
| `lockname` | Optional diagnostics context. |
| `start_slot` | Optional diagnostics context. |

The public shim accepts legacy arguments and builds this request. Phase helpers
accept the request or smaller derived contexts, not the original eight
parameters.

### StatelessPlanRequest

**Owner**: `stateless.py`

Bundles the `compute_stateless_plan` inputs: observed slots, desired
reservations, max events, plan id, generated timestamp, and prefix. The public
function may keep the existing six-parameter signature while internal helpers use
this request object.

## State Transitions Preserved by the Split

### Desired reservation selection

```text
Reservation list
  └─ filter eligible / not checked_out / missing-count tolerance
      └─ protected active first
          └─ remaining capacity filled by soonest non-protected
              ├─ selected -> stable-name matching / assignment
              └─ overflow -> capacity or no_empty_slot
```

### Physical slot reconciliation

```text
Readable empty slot ── selected unmatched desired ──► SET/ASSIGN
Readable occupied matching stable name ─────────────► NOOP / UPDATE_TIMES /
                                                      OVERWRITE_MANUAL_CHANGE /
                                                      UPDATE_IN_PLACE
Readable occupied no selected match ────────────────► CLEAR/RESET stale
Readable duplicate non-canonical match ─────────────► CLEAR/RESET duplicate
Pending clear ──────────────────────────────────────► RETRY_CLEAR or BLOCKED
Unreadable / blocked ───────────────────────────────► BLOCKED
```

### Confirmed reset before reapply

```text
matched slot needs replacement PIN/name
  └─ action remains bound to same physical slot
      └─ preflight read
          └─ clear/reset
              └─ reapply only after physical empty confirmation
                  └─ otherwise retry/block from next observed physical state
```

No Store field transitions a slot between empty, occupied, blocked, reset, or
assignable states.
