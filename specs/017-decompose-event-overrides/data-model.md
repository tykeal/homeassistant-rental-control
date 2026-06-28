<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Data Model: Decompose Event Overrides

This feature is a behavior-preserving refactor. The models below are internal
implementation aids, not new public API. `EventOverrides`, `EventOverride`, and
`ReserveResult` remain importable from `custom_components.rental_control.event_overrides`.

## Existing public entities retained on `event_overrides.py`

### EventOverrides

**Owner module**: `event_overrides.py`

**Fields/state retained**:

- slot configuration: `_start_slot`, `_max_slots`, `_next_slot`, `_ready`
- assignment state: `_overrides`, `_slot_uids`, `_slot_miss_counts`
- trim/prefix state: `_trim_names`, `_max_name_length`, `_event_prefix`,
  `_prefix_length`
- reconciliation state: `_pending_fences`, `_pending_clear_slots`,
  `_actual_state_cache`, `_reconciliation_active`, `_diagnostics_snapshot`
- retry/error state: `_retry_counts`, `_escalated`, `_last_slot_errors`
- feedback suppression: `_suppressed_state_changes`
- concurrency boundary: `_lock`

**Relationships**:

- consumes reconciliation `DesiredPlan`, `Reservation`, `SlotAction`, and
  `ActionKind`
- calls util service helpers `async_fire_clear_code`, `async_fire_set_code`, and
  `async_fire_update_times`
- provides the FR-017 compatibility surface to coordinator, util, sensors, and
  tests

**Validation rules**:

- public and private compatibility members stay available from the class
- shell applies all state mutations and HA/Keymaster side effects in current
  order
- no helper module becomes a required production import boundary

### EventOverride

**Owner module**: `event_overrides.py` public alias; internal snapshots in
`event_overrides_helpers.models`

**Fields**:

- `slot_name: str`
- `slot_code: str`
- `start_time: datetime`
- `end_time: datetime`

**Validation rules**:

- dictionary shape remains unchanged for existing tests and callers
- raw slot code is never copied into diagnostics snapshots

### ReserveResult

**Owner module**: `event_overrides.py`

**Fields**:

- `slot: int | None`
- `is_new: bool`
- `times_updated: bool`

**Validation rules**:

- returned values match the retired greedy path exactly

## New internal helper entities

### TrimConfig

**Owner module**: `event_overrides_helpers.models` or `trim.py`

**Fields**:

- `trim_names: bool`
- `max_name_length: int`
- `event_prefix: str`
- `prefix_length: int`
- derived `guest_max: int`

**Relationships**:

- used by matcher, ownership checks, verify-slot ownership, and trim helpers

**Validation rules**:

- `guest_max` remains `max_name_length - prefix_length`
- prefix stripping remains deterministic `event_prefix + " "` removal
- trim comparison delegates to `trim_name`, preserving word-boundary and hard
  truncation behavior

### OverrideSnapshot

**Owner module**: `event_overrides_helpers.models`

**Fields**:

- `slot: int`
- `slot_name: str`
- `slot_code_present: bool` or redacted code marker for diagnostics only
- `start_time: datetime`
- `end_time: datetime`
- `uid: str | None`

**Relationships**:

- built by `EventOverrides` from `_overrides` and `_slot_uids`
- consumed by matcher, cleanup, same-start, and diagnostics helpers

**Validation rules**:

- UIDs are normalized before comparison
- slot ordering follows current sorted occupied-slot order
- snapshots are read-only inputs; helpers do not mutate `_overrides`

### MatchCatalog

**Owner module**: `event_overrides_helpers.matcher`

**Fields**:

- ordered `OverrideSnapshot` records
- `TrimConfig`
- optional `exclude_slot`

**Relationships**:

- passed to all phase functions
- contains enough data to compute exact UID owners and same-start preferred slots
  without calling back into the shell

**Validation rules**:

- occupied slots are sorted exactly as `__get_slots_with_values()` returns today
- excluded slots are skipped in all phases and ownership checks

### MatchRequest

**Owner module**: `event_overrides_helpers.models`

**Fields**:

- `event: EventIdentity`
- `exclude_slot: int | None`
- `target_slot: int | None` for mirror checks

**Relationships**:

- `_find_overlapping_slot` creates a request with no target slot and expects the
  winning slot
- `_slot_has_matching_event` creates a request for each current event with the
  checked slot as the target

**Validation rules**:

- event names are already prefix-stripped by callers where current code strips
  them
- strict interval overlap uses UTC-normalized datetimes and the predicate
  `start_a < end_b AND start_b < end_a`

### MatchResult

**Owner module**: `event_overrides_helpers.models`

**Fields**:

- `slot: int | None`
- `phase: MatchPhase | None`
- `restored_slot_name: str | None`
- `reason: str` for tests/debug assertions only if useful

**Relationships**:

- returned by shared matcher to both mirror wrappers
- shell applies `restored_slot_name` to `_overrides[slot]["slot_name"]` only when
  current code would restore the longer full name

**Validation rules**:

- phase order is UID-positive exact-name, exact-name strict-overlap, then
  trim-aware fallback
- exact UID owner precedence beats same-start fallback candidates
- same-start bypass only applies when UTC starts match

### MatchPhase

**Owner module**: `event_overrides_helpers.models`

**Values**:

- `UID_EXACT_NAME`
- `EXACT_NAME_STRICT_OVERLAP`
- `TRIM_UID`
- `TRIM_STRICT_OVERLAP`

**Validation rules**:

- enum values are internal and must not change public operation results or
  diagnostics unless tests intentionally use them for helper-only assertions

### SlotReservationRequest

**Owner module**: `event_overrides_helpers.models`

**Fields**:

- `slot_name: str`
- `slot_code: str`
- `start_time: datetime`
- `end_time: datetime`
- `uid: str | None = None`
- `prefix: str | None = None`

**Relationships**:

- normalized from legacy `async_reserve_or_get_slot` calls
- consumed by the retired greedy reservation shell

**Validation rules**:

- supports current keyword calls and four-positional-plus-`uid` test calls
- unknown legacy keywords fail in focused wrapper tests
- prefix stripping and UID normalization happen at the same point as today

### SlotUpdateRequest

**Owner module**: `event_overrides_helpers.models`

**Fields**:

- `slot: int`
- `slot_code: str`
- `slot_name: str`
- `start_time: datetime`
- `end_time: datetime`
- `prefix: str | None = None`

**Relationships**:

- normalized from legacy `async_update` and `update` calls
- consumed by async and synchronous update shells

**Validation rules**:

- supports coordinator positional prefix calls, util reset calls, test keyword
  calls, and synchronous `prefix=` test calls
- duplicate redirect in `async_update` still uses matcher with `exclude_slot`
- synchronous `update` keeps copy-on-write semantics

### EvictionDecision

**Owner module**: `event_overrides_helpers.models` or `greedy_cleanup.py`

**Fields**:

- `slot: int`
- `action: reset_miss | increment_miss | clear | preserve`
- `new_miss_count: int | None`
- `reason: missing_event | threshold | empty_calendar | malformed_window |
  past_end | beyond_boundary`

**Relationships**:

- computed by cleanup helpers from slot snapshots, event identities, calendar
  boundaries, current date, and current miss counts
- applied by `async_check_overrides`

**Validation rules**:

- future missing slots increment miss count until `SLOT_MISS_THRESHOLD`
- matched slots reset miss count
- past, malformed, empty-calendar, and beyond-boundary decisions clear at the
  same time as current source
- unconfirmed or failed clear results leave slot occupied and miss state
  conservative

### PlanDispatchDecision

**Owner module**: `event_overrides_helpers.apply_dispatch`

**Fields**:

- `action: SlotAction`
- `operation: skip | clear | set | update_times | overwrite`
- `reservation: Reservation | None`
- `warning: str | None`

**Relationships**:

- produced for each `plan.actions` item in order
- shell executes the selected operation and appends operation results in current
  order

**Validation rules**:

- `NOOP` and `BLOCKED` skip without result
- missing reservations for set/update/overwrite skip with current warning
- clear warning reason strings remain unchanged

### ClearApplicationDecision

**Owner module**: `event_overrides_helpers.apply_clear`

**Fields**:

- `slot: int`
- `operation_id: str`
- `expected_name: str | None`
- `preflight_result: OperationResult | None`
- `state_mutations: list[StateMutation]`
- `error: str | None`

**Relationships**:

- shell creates operation fence and pending-clear token, performs fresh HA reads,
  calls `async_fire_clear_code`, then applies returned mutations

**Validation rules**:

- confirmed preflight empty releases pending clear and returns confirmed clear
- stale tokens produce unconfirmed clear without applying stale service results
- confirmed clear frees override/UID/miss/error state but does not recompute
  `next_slot` in reconciliation

### SetApplicationDecision

**Owner module**: `event_overrides_helpers.apply_set`

**Fields**:

- `slot: int`
- `operation_id: str`
- `tentative_override: EventOverride`
- `suppression_changes: dict[str, str]`
- `rollback_on_failure: bool`
- `error: str | None`

**Relationships**:

- shell verifies confirmed-empty physical state, applies tentative assignment,
  suppresses expected feedback, calls `async_fire_set_code`, and applies result
  mutations

**Validation rules**:

- no service call happens when slot is not confirmed empty
- failed sets rollback tentative assignment and clear UID state
- unconfirmed sets keep tentative assignment and clear the pending fence exactly
  as current source does

### UpdateApplicationDecision

**Owner module**: `event_overrides_helpers.apply_update`

**Fields**:

- `slot: int`
- `suppression_changes: dict[str, str]`
- `cached_start: datetime | None`
- `cached_end: datetime | None`
- `drift_fields: list[str]`
- `replacement_plan_id: str | None`

**Relationships**:

- used by update-times, overwrite-manual-change, and update-in-place wrappers

**Validation rules**:

- confirmed update-times mutates cached buffered start/end only
- overwrite/update-in-place logs drift fields without raw PIN values
- replacement set runs only after clear is physically confirmed

### DiagnosticsSnapshot

**Owner module**: `event_overrides_helpers.diagnostics`

**Fields**:

- `plan_id`
- `generated_at`
- `matched_slots`
- `pending_corrections`
- `manual_drift_slots`
- `pending_clear_slots`
- `slot_retry_counts`
- `last_slot_errors`

**Relationships**:

- pure projection from `DesiredPlan` plus shell retry/error/pending state
- stored in `EventOverrides._diagnostics_snapshot`

**Validation rules**:

- raw slot codes are excluded
- retry counts cover the same slot range from `start_slot` to max slots
- pending clear slots are sorted as today
