<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Data Model: Stateless Slot Reconciliation

## Entities

### ObservedSlot

A physical Keymaster slot in the Rental-Control-managed range, read during the
current refresh. This replaces the current `ManagedSlot` fields that mix
physical state with persisted mapping status (`reconciliation.py:252-319`).

| Field | Type | Purpose |
|-------|------|---------|
| `slot` | `int` | Physical Keymaster slot number. |
| `managed` | `bool` | True only inside `start_slot .. start_slot + max_events - 1`. |
| `raw_name` | `str | None` | Raw Keymaster name text state for this refresh; never from Store. |
| `raw_pin` | `str | None` | Raw Keymaster PIN in memory only for this refresh; never logged or persisted. |
| `has_pin` | `bool | None` | True when PIN is non-empty, false when confirmed blank/unknown/None, none when unreadable. |
| `actual_start` | `datetime | None` | Observed Keymaster date-range start when readable. |
| `actual_end` | `datetime | None` | Observed Keymaster date-range end when readable. |
| `date_range_enabled` | `bool | None` | Observed Keymaster date-range switch state. |
| `enabled` | `bool | None` | Observed Keymaster slot-enabled switch state. |
| `readable` | `bool` | False when any safety-critical state is `unavailable` or missing. |
| `empty_confirmed` | `bool` | True only when both name and PIN are cleared by util helpers. |
| `classification` | `ObservedSlotStatus` | `empty`, `occupied`, `phantom`, or `unknown`. |
| `normalized_name_forms` | `set[str]` | Full/prefix-stripped/trim-aware normalized names for matching. |
| `matched_desired_id` | `str | None` | Desired reservation matched by stable name in this refresh. |

**Validation rules**:

- Unmanaged slots are emitted for diagnostics at most and are never modified.
- `empty_confirmed` requires both name and PIN to satisfy
  `is_cleared_keymaster_text_state()` (`util.py:76-79`).
- `unavailable` makes `readable == False` via
  `is_unreadable_keymaster_text_state()` (`util.py:82-84`) and forces
  `classification == unknown`.
- `raw_pin` is memory-only and must not appear in Store, logs, diagnostics, or
  sensor attributes.
- A slot with `classification == occupied` cannot receive a different
  reservation until a reset is confirmed empty.

### DesiredReservation

A calendar stay that should be considered for Keymaster programming during the
current refresh.

| Field | Type | Purpose |
|-------|------|---------|
| `desired_id` | `str` | Refresh-local stable identifier: normalized stable slot name plus start-order index for duplicate names. |
| `stable_slot_name` | `str` | Unprefixed, untrimmed slot name from `get_slot_name()` (`util.py:784-847`). |
| `display_slot_name` | `str` | Prefixed/trimmed Keymaster display name, matching `async_fire_set_code()` (`util.py:478-489`). |
| `normalized_name_forms` | `set[str]` | Stable full and display forms used to match `ObservedSlot` names. |
| `start` | `datetime` | Reservation access start before buffer. |
| `end` | `datetime` | Reservation access end before buffer. |
| `buffered_start` | `datetime` | Keymaster date-range start after `apply_buffer()` (`util.py:442-463`). |
| `buffered_end` | `datetime` | Keymaster date-range end after `apply_buffer()`. |
| `slot_code` | `str` | Desired PIN for this refresh; generated or manual override. |
| `code_source` | `generated | manual_observed | manual_config` | Why `slot_code` was chosen; never persisted raw. |
| `event_uid` | `str | None` | Optional current iCal UID for cache-only aliases. |
| `booking_aliases` | `set[str]` | Optional booking IDs for cache-only diagnostics. |
| `eligible` | `bool` | True when existing calendar parsing/config includes the reservation. |
| `protected_active` | `bool` | True for current checked-in guest from check-in sensor state. |
| `checked_out` | `bool` | True when check-in sensor says the stay checked out. |
| `selected_rank` | `int | None` | Rank in the should-be set after active protection and soonest-N selection. |
| `matched_slot` | `int | None` | Physical slot matched by stable name this refresh. |
| `assigned_slot` | `int | None` | Physical slot planned to contain this reservation after actions. |
| `sensor_lookup_keys` | `set[str]` | Current-event fingerprints, UID aliases, and compatibility keys used by `event_N` sensors to find this desired record. |
| `physical_time_override` | `tuple[datetime, datetime] | None` | Access window derived from a matched physical slot after reversing buffers when manual time override semantics apply. |
| `overflow_reason` | `str | None` | Why the reservation is not selected or assignable. |

**Validation rules**:

- `start < end` after timezone normalization.
- `display_slot_name` must be exactly the value the set helper will write.
- Duplicate `stable_slot_name` values are allowed; `desired_id` disambiguates by
  start-time order under the whole-unit non-overlap assumption. If trimmed-name
  collisions, group-count mismatches, or reordered duplicate/date-shift groups
  prevent one deterministic pairing, the affected group is blocked and
  diagnosed rather than guessed.
- `protected_active` reservations are selected before non-protected
  reservations and count against capacity.
- `slot_code` is never read from Store. If a matched physical slot contains a
  manual PIN, the PIN may be copied in memory for this refresh only.

### SlotAction

A stateless per-slot decision emitted by the planner. This replaces the current
`ActionKind` set/update/clear model that relies on persisted slot ownership
(`reconciliation.py:106-135`, `reconciliation.py:1453-1536`).

| Field | Type | Purpose |
|-------|------|---------|
| `kind` | `noop | update_in_place | reset | assign | blocked` | Action category for this slot. |
| `slot` | `int` | Physical Keymaster slot number. |
| `desired_id` | `str | None` | Desired reservation involved in the action. |
| `matched_by` | `name_exact | name_trimmed | name_prefixed | duplicate_order | none` | How identity was established. |
| `requires_confirmed_empty` | `bool` | True before any replacement PIN can be written. |
| `reason` | `str | None` | Diagnostic reason such as stale, duplicate, phantom, drift, or unreadable. |
| `sequence` | `list[PhysicalOperation]` | Ordered service operations for apply: clear, update_times, set, or none. |
| `preflight_read` | `bool` | True when apply must re-read physical name/PIN immediately before the operation. |
| `blocked_reason` | `str | None` | Why no physical write is safe this cycle. |

**Action semantics**:

- `noop`: physical name, PIN policy, and date window already match the desired
  reservation; no service call.
- `update_in_place`: physical name matches the desired reservation. Date-only
  drift may use `update_times`. PIN or display-name replacement performs
  `fresh read -> clear -> confirm empty -> fresh read -> set same desired to
  same slot` in the same apply path. If clear is not confirmed, the set is
  skipped and the next refresh retries from physical state.
- `reset`: physical slot contains no selected desired reservation, contains a
  duplicate non-canonical match, contains phantom state, or drifted to an
  unrelated name. Apply clears only; reassignment waits until a later or same
  apply phase sees confirmed empty.
- `assign`: desired reservation is not physically present by name and this slot
  is already confirmed empty. Apply re-checks name and PIN immediately before
  writing, then sets name, PIN, dates, and enabled state.
- `blocked`: slot is unreadable, still occupied while waiting for reset, outside
  managed scope, or otherwise unsafe. No assignment is made.

### StatelessPlan

The planner's refresh result.

| Field | Type | Purpose |
|-------|------|---------|
| `plan_id` | `str` | Refresh-scoped identifier for logs and callback suppression. |
| `generated_at` | `datetime` | Plan computation timestamp. |
| `observed_slots` | `dict[int, ObservedSlot]` | Current physical facts by slot. |
| `desired_reservations` | `dict[str, DesiredReservation]` | Current desired reservations by refresh-local ID. |
| `selected` | `dict[str, int]` | Desired IDs planned into slots after the action sequence. |
| `overflow` | `dict[str, str]` | Eligible desired reservations not selected or not assignable. |
| `actions` | `list[SlotAction]` | Per-slot actions ordered reset/update before assign. |
| `diagnostics` | `dict[str, Any]` | Redacted explanation of matches, resets, blocks, and overflow. |

**Plan invariants**:

- Every selected desired reservation appears in at most one slot.
- Every physical managed slot has at most one action.
- A non-empty slot never receives a different reservation until an empty
  physical state is confirmed.
- Existing matched reservations are updated in their physical slot rather than
  causing a second assignment; ambiguous matches block rather than allocate a
  duplicate.
- Store/cache data does not participate in `selected`, `actions`, or overflow
  computation.

### CacheOnlyStoreRecord

Optional HA Store cache record. The current schema/key may remain
(`const.py:138-146`) but its semantics change.

| Field | Type | Purpose |
|-------|------|---------|
| `schema_version` | `int` | Cache schema version. Existing v1 files are accepted as cache input. |
| `entry_id` | `str` | Integration config entry scope. |
| `lockname` | `str | None` | Keymaster lock scope for diagnostics only. |
| `updated_at` | `str` | Last successful cache write. |
| `aliases` | `dict[str, AliasRecord]` | UID/booking aliases keyed by normalized stable name and start order. |
| `last_plan` | `dict[str, Any]` | Redacted last stateless plan diagnostics. |
| `migration_notes` | `list[str]` | Notes about ignored legacy status/fence fields. |

**Cache invariants**:

- Raw PIN values are never stored.
- Legacy fields `status`, `slot`, `operation_id`, `operation_kind`,
  `pending_clear_since`, `blocked_slots`, and `missing_count` are ignored by the
  planner.
- Loading failure, missing cache, duplicate cache claims, stale cache, or cache
  deletion cannot change physical actions.
- Cache writes are best-effort and can be skipped without changing runtime
  correctness.

## Matching and Identity Rules

1. Build desired reservations from the sorted calendar using existing parsing,
   Honor Event Times, override fallback, and buffer rules.
2. Build observed slots by reading physical Keymaster name, PIN, dates, and
   switches for every managed slot.
3. Normalize desired and observed names:
   - strip surrounding whitespace and casefold;
   - remove configured prefix plus separator from observed names when present;
   - compare desired full name, desired display name, observed full name, and
     observed prefix-stripped name;
   - when trimming is enabled, accept `trim_name(longer, guest_max) == shorter`.
4. Group desired reservations and observed occupied slots by normalized stable
   name. For each group, pair by start-time order. If physical start is missing,
   slot number is the deterministic fallback. If trim-colliding full names or
   simultaneous duplicate-name date shifts cannot produce a single safe order,
   block the group and report ambiguity.
5. Matched pairs get `update_in_place` or `noop`; unmatched occupied physical
   slots get `reset` or `blocked`; unmatched desired reservations get `assign`
   only into confirmed-empty slots.
6. Duplicate physical slots for one desired reservation keep one canonical match
   selected by active protection, then earliest start-order pair, then lowest
   slot. Non-canonical duplicates reset through confirmed clear.

## State Transitions

### Physical Slot Lifecycle

```text
UNKNOWN ──readable empty──────────────► EMPTY
UNKNOWN ──readable occupied───────────► OCCUPIED
UNKNOWN ──unavailable/missing─────────► BLOCKED

EMPTY ──assign desired────────────────► OCCUPIED (after set confirmed)
EMPTY ──no desired────────────────────► EMPTY

OCCUPIED ──same desired correct───────► OCCUPIED (noop)
OCCUPIED ──same desired date drift────► OCCUPIED (update_times)
OCCUPIED ──same desired PIN drift─────► CLEARING_SAME_SLOT
OCCUPIED ──not desired/duplicate──────► CLEARING_EMPTY

CLEARING_SAME_SLOT ──empty confirmed──► OCCUPIED (set same desired same slot)
CLEARING_SAME_SLOT ──not confirmed────► OCCUPIED/BLOCKED next refresh
CLEARING_EMPTY ──empty confirmed──────► EMPTY
CLEARING_EMPTY ──not confirmed────────► OCCUPIED/BLOCKED next refresh
```

There is no persisted `pending_clear` state in this machine. A slot that did
not clear remains occupied or unknown when physically observed later.

### Desired Reservation Lifecycle

```text
calendar eligible ──active checked-in──────► selected protected
calendar eligible ──within soonest-N───────► selected unprotected
calendar eligible ──beyond capacity────────► overflow capacity
selected ──stable name matched physically──► matched existing slot
selected ──unmatched + empty slot──────────► assign
selected ──unmatched + no empty slot───────► blocked no_empty_slot
calendar removed/not eligible──────────────► no desired reservation
```

Active checked-in reservations are selected before non-protected reservations
and count against capacity. Removed/non-eligible reservations have no ghost
reservation synthesized from Store.

### Cache-Only Store Lifecycle

```text
missing/corrupt/legacy Store ──load────────► empty cache + migration note
cache aliases available ──load─────────────► optional diagnostics only
refresh complete ──best-effort save────────► redacted aliases/last_plan
cache deleted mid-run ──next refresh───────► empty cache, same actions
```

Store records never transition a physical slot between empty, occupied,
blocked, reset, or assign states.
