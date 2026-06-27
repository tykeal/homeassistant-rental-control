<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Data Model: Decompose Coordinator

This feature is a behavior-preserving refactor. The data model describes
internal helper objects and existing coordinator-owned structures; it does not
introduce new persisted schemas, public APIs, entities, services, or diagnostics
fields.

## Public Coordinator Shell

**Owner**: `custom_components/rental_control/coordinator.py`

**Entity**: `RentalControlCoordinator`

**Role**: Home Assistant `DataUpdateCoordinator` shell and compatibility
boundary.

**Existing public members retained**:

- identity and metadata: `monitored_locknames`, `entry_id`, `unique_id`,
  `version`, `device_info`, `created`;
- config and runtime attributes: `lockname`, `start_slot`, `max_events`,
  `event_prefix`, `code_generator`, `code_length`, `code_buffer_before`,
  `code_buffer_after`, `trim_names`, `max_name_length`;
- refresh and entity data: inherited `data`, `name`, `hass`,
  `last_update_success`, `async_config_entry_first_refresh`, `async_refresh`,
  `async_request_refresh`, plus `event` and `async_get_events`;
- reconciliation and diagnostics: `latest_plan`, `latest_overflow`,
  `latest_reconciliation_diagnostics`, `keymaster_event_diagnostics`,
  `get_slot_assignment`, `get_slot_code`, `get_overflow_reason`;
- Store and Keymaster methods: `async_setup_keymaster_overrides`,
  `async_load_slot_store`, `get_persisted_slot_mappings`,
  `async_save_slot_store`, `async_adopt_keymaster_slots`,
  `update_config`, `update_event_overrides`, and `event_overrides`.

**State rules**:

- Owns all HA state reads and writes, Store writes, refresh scheduling, service
  calls, and `hass.data` lookups.
- Applies helper decisions in the same order as the current source.
- Remains importable as `from .coordinator import RentalControlCoordinator`.

## CalendarParseContext

**Owner**: `coordinator_helpers/models.py`

**Used by**: `coordinator_helpers/calendar_parsing.py`

**Fields**:

- `timezone`: coordinator timezone.
- `checkin`: configured default check-in time.
- `checkout`: configured default checkout time.
- `event_prefix`: optional configured prefix.
- `ignore_non_reserved`: whether Blocked/Not available events are skipped.
- `honor_event_times`: whether PMS and description times override defaults.
- `code_buffer_before` / `code_buffer_after`: current buffer minutes.
- `override_lookup`: optional callback returning a physical override by slot
  name.

**Validation rules**:

- Times and timezone come from config flow/coordinator parsing and are already
  valid.
- Helper output must match current `CalendarEvent` fields, UID normalization, and
  sorted order.

## ReservationBuildContext

**Owner**: `coordinator_helpers/models.py`

**Used by**: `coordinator_helpers/reservations.py`

**Fields**:

- `entry_id`: coordinator entry id for reservation fingerprints.
- `event_prefix`, `trim_names`, `max_name_length`: display-name controls.
- `code_buffer_before` / `code_buffer_after`: buffer minutes.
- `should_update_code`: current generated-code replacement flag.
- `coerce_event_datetime`: callback preserving date-to-datetime behavior.
- `generate_slot_code`: callback preserving generated-code behavior.
- `active_windows_for_name`: callback or mapping of active check-in windows.
- `slot_query_factory`: creates `ObservedSlotQuery` for physical matching.

**Validation rules**:

- Reservation identities use `make_reservation_fingerprint()` unchanged.
- Alias extraction uses reconciliation package helpers unchanged.
- Raw PINs are not read from Store or reconstructed for ghosts.

## ObservedSlotQuery

**Owner**: `coordinator_helpers/models.py`

**Used by**: `coordinator_helpers/slot_matching.py` and the coordinator
compatibility wrapper.

**Fields**:

- `managed_slots`: current physical `ManagedSlot` observations.
- `slot_name`: logical guest/slot name.
- `display_slot_name`: Keymaster display name after prefix/trimming.
- `consumed_slots`: optional mutable set of physical slots already paired.
- `desired_start` / `desired_end`: optional desired buffered window.
- `require_date_match`: duplicate-name date-match mode.
- `reserved_date_windows`: reserved windows for shifted-date filtering.
- `ordered_date_windows`: canonical ordered windows for duplicate pairing.
- `block_unknown_date_fallback`: disables unsafe unknown-date fallback.
- `expected_name_count`: expected number of same-name reservations.
- `event_prefix`: prefix used to compare physical and logical names.

**Validation rules**:

- Matching preserves normalized name forms from
  `normalize_slot_name_for_fingerprint()`.
- Consumed slots are updated only when a slot is returned.
- Duplicate-name and unknown-date fallbacks match current source behavior.

## EventOverrideUpdate

**Owner**: `coordinator_helpers/models.py`

**Used by**: `RentalControlCoordinator.update_event_overrides()`.

**Fields**:

- `slot`: Keymaster slot number.
- `slot_code`: observed or generated PIN string.
- `slot_name`: physical or logical slot name string.
- `start_time`: effective Keymaster start datetime.
- `end_time`: effective Keymaster end datetime.

**Validation rules**:

- Compatibility wrapper accepts the dataclass, current five positional values, or
  current keyword values.
- `request_refresh` remains a coordinator method option, preserving bootstrap's
  `request_refresh=False` and util.py's default refresh request.

## KeymasterSlotSnapshot

**Owner**: `coordinator_helpers/models.py`

**Used by**: `coordinator_helpers/keymaster_observation.py` and
`coordinator_helpers/keymaster_bootstrap.py`.

**Fields**:

- `slot`: physical Keymaster slot number.
- `name_state`: HA text state for the slot name, or missing.
- `pin_state`: HA text state for the PIN, or missing.
- `use_date_range_state`: HA switch state for date-range limits, or missing.
- `enabled_state`: HA switch state for slot enabled, or missing.
- `start_state` / `end_state`: HA datetime states, or missing.

**Derived structures**:

- `ManagedSlot` for reconciliation input.
- Actual-state diagnostics dict passed to `EventOverrides.update_actual_state()`.
- Bootstrap/adoption decisions for override setup and Store mappings.

**Validation rules**:

- Unreadable text states classify as `UNKNOWN` and do not expose stale values.
- Blank Keymaster text states remain equivalent to current helper behavior.
- Date ranges are parsed only when date-range limits are on.

## BootstrapDecision

**Owner**: `coordinator_helpers/models.py`

**Used by**: `coordinator_helpers/keymaster_bootstrap.py`.

**Fields**:

- `slot`: Keymaster slot number.
- `override_update`: optional `EventOverrideUpdate` for readable slot state.
- `force_clear`: whether a partially reset slot must be cleared first.
- `placeholder_name`: optional adopted placeholder for code-bearing unnamed slots.
- `skip_reason`: optional unreadable/missing-state explanation for tests and logs.

**State transitions**:

- Partially reset name-only/date-range-off slots request a forced clear and then
  register as empty.
- Code-bearing unnamed slots become occupied placeholders to avoid unsafe reuse.
- Unreadable slots are skipped as today.

## AdoptionMappingDecision

**Owner**: `coordinator_helpers/models.py`

**Used by**: `coordinator_helpers/keymaster_bootstrap.py`.

**Fields**:

- `identity_key`: `adopted.<entry_id>.slot<N>` key.
- `mapping`: Store mapping dict without raw PINs.
- `slot`: physical slot number.
- `status`: `occupied` or `pending_clear`.
- `skip_reason`: optional existing-slot/empty/unreadable reason.

**Validation rules**:

- Raw PIN values are represented only as `has_code: True/False`.
- Existing Store slots are not overwritten.
- Missing Store metadata is initialized with the same schema fields and timestamps
  as the current coordinator.

## CheckinProtectionSnapshot

**Owner**: `coordinator_helpers/models.py`

**Used by**: `coordinator_helpers/checkin_protection.py`.

**Fields**:

- `state`: check-in sensor state.
- `guest_name`: tracked guest name.
- `start` / `end`: parsed tracked event datetimes.
- `summary`: tracked summary fallback for synthesized reservations.
- `attributes`: original sensor attributes needed for parity.

**Decision output**:

- `mark_protected(identity_key)`.
- `mark_checked_out(identity_key)`.
- `append_synthesized_reservation(reservation)`.
- `no_change`.

**Validation rules**:

- Exact start/end matches win for duplicate names.
- Unique name match is allowed only when tracked times are unavailable.
- Synthesized active stays require a safe physical match and preserve manual code
  source when an actual physical code is observed.

## StoreSyncPlan

**Owner**: `coordinator_helpers/models.py`

**Used by**: `coordinator_helpers/store_sync.py`.

**Fields**:

- `remove_identity_keys`: stale or confirmed-cleared mapping keys to delete.
- `upsert_mappings`: cache-only mappings to write for selected reservations.
- `metadata`: schema version, entry id, lockname, start slot, max slots,
  `updated_at`, aliases, migration notes, and latest plan diagnostics.

**Validation rules**:

- Confirmed clears remove mappings before selected reservations are upserted.
- Failed sets do not advance Store assignment metadata.
- Store remains cache-only and never overrides current physical Keymaster state.

## Existing reconciliation structures

**Owner**: `custom_components/rental_control/reconciliation/`

**Used by**: coordinator shell and helper modules.

**Structures retained**:

- `DesiredPlan`
- `ManagedSlot`
- `Reservation`
- `SlotStatus`
- `compute_desired_plan`
- `extract_booking_aliases`
- `make_reservation_fingerprint`
- `normalize_slot_name_for_fingerprint`

**Rules**:

- No coordinator helper redefines reconciliation algorithms.
- All helper-produced `Reservation` and `ManagedSlot` values must be equivalent
  to current coordinator-produced values for identical inputs.
