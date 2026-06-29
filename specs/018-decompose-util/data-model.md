<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Data Model: Decompose Util

## Util Compatibility Surface

**Purpose**: Stable import and monkeypatch boundary exposed by
`custom_components.rental_control.util`.

**Fields/exports**:

- Public helpers and types listed in FR-003 of the specification.
- Compatibility attributes patched by current tests: `asyncio`,
  `async_track_state_change_event`, `pn_create`, `pn_dismiss`, and
  `_SET_CODE_CONFIRMATION_TIMEOUT`.
- Wrapper functions for `async_fire_set_code`, `async_fire_clear_code`,
  `async_fire_update_times`, `get_event_identities`, `get_event_names`, and
  `handle_state_change`.

**Validation rules**:

- Every current production and test import from `util.py` succeeds unchanged.
- Patching `util.async_fire_*` and `util.get_event_identities` intercepts callers
  that are not patched at a closer module boundary.
- No wrapper changes service-call ordering, return values, exceptions, or logs
  relied on by tests.

## KeymasterServiceDeps

**Purpose**: Dependency bundle supplied by `util.py` to extracted Keymaster
service implementations so util-level patches remain effective.

**Fields**:

- `sleep`: async sleep callable, normally `util.asyncio.sleep`.
- `track_state_change`: Home Assistant state tracker, normally
  `util.async_track_state_change_event`.
- `confirmation_timeout`: float timeout, normally
  `util._SET_CODE_CONFIRMATION_TIMEOUT`.
- `create_notification`: persistent-notification create callable, normally
  `util.pn_create`.
- `dismiss_notification`: persistent-notification dismiss callable, normally
  `util.pn_dismiss`.
- `logger`: utility logger used for existing messages.

**Validation rules**:

- Helper signatures consume one dependency object instead of many dependency
  parameters.
- Patched util attributes are read when the public util wrapper is called, not at
  module import time.

## KeymasterOperationRequest

**Purpose**: Normalized request for one physical slot operation.

**Fields**:

- `coordinator`: current Rental Control coordinator-like object.
- `slot`: Keymaster slot number.
- `event`: calendar event adapter for set/update operations, or `None` for clear.
- `expected_name`: optional expected owner name for clear.
- `kind`: one of `set`, `clear`, or `update_times`.

**Relationships**:

- Produces `OperationResult`.
- Uses `BufferedWindow` for set and update-times operations.
- Uses `ClearStateSnapshot` for clear confirmation.

**Validation rules**:

- Missing lock name returns the same unconfirmed result as today.
- Ownership verification failure returns the same unconfirmed result and performs
  no unsafe service write.
- Cancellation propagates; ordinary exceptions map to existing failed or
  unconfirmed result behavior.

## BufferedWindow

**Purpose**: Start and end datetimes after existing buffer and coercion logic.

**Fields**:

- `start`: timezone-aware `datetime` used for Keymaster start entity.
- `end`: timezone-aware `datetime` used for Keymaster end entity.

**Validation rules**:

- `apply_buffer` returns original values unchanged when both buffers are zero.
- Bare `date` values are normalized through coordinator timezone handling only
  when arithmetic or service writes require datetimes.
- Invalid values raise the same `ValueError`/`TypeError` paths and produce the
  same failed `OperationResult` classifications.

## ServiceCallPlan

**Purpose**: Ordered service-call description for set/update helper phases.

**Fields**:

- `domain`: Home Assistant service domain.
- `service`: service name.
- `target`: entity id target.
- `data`: service data dictionary.

**Validation rules**:

- Set operation order remains disable slot, enable date range, write end, write
  start, write PIN, write name, enable slot.
- Update-times operation order remains end before start.
- Clear operation still uses the reset button followed by current propagation and
  lingering-state checks.

## ClearStateSnapshot

**Purpose**: Current Keymaster text-state observation after reset/force-clear.

**Fields**:

- `name_state`: raw name state object or `None`.
- `pin_state`: raw PIN state object or `None`.
- `name_unconfirmed`: true for missing or unreadable name state.
- `pin_unconfirmed`: true for missing or unreadable PIN state.
- `lingering_name`: true when a readable non-cleared name remains.
- `lingering_pin`: true when a readable non-cleared PIN remains.

**Validation rules**:

- Missing or unreadable states produce unconfirmed results, not confirmed clears.
- Lingering name and PIN flags match current `OperationResult` fields.
- Forced name clear is attempted only for a readable non-cleared name.

## StateHandlerDeps

**Purpose**: Dependency bundle supplied by `util.handle_state_change` to the
extracted state handler.

**Fields**:

- `sleep`: async sleep callable, normally `util.asyncio.sleep`.
- `logger`: utility logger.

**Validation rules**:

- The 0.1-second settle delay uses `sleep` from `util.py` so current tests can
  patch it.
- No dependency object introduces new Home Assistant side effects.

## StateChangeContext

**Purpose**: Normalized input for one Keymaster state-change callback.

**Fields**:

- `hass`: Home Assistant instance.
- `config_entry`: config entry.
- `event`: original state-changed event.
- `coordinator`: resolved Rental Control coordinator.
- `lockname`: coordinator lock name.
- `entity_id`: changed entity id.
- `slot`: extracted slot number.
- `new_value`: current event new state value or `None`.
- `has_new_value`: whether `new_value` is concrete.
- `existing_override`: current override dict for the slot, if any.

**Validation rules**:

- Missing lock name or event overrides returns early.
- Non-matching entity ids log the same warning and return early.
- Reset entities call `event_overrides.async_update` with empty code/name and
  current local-day start/end values.

## SlotStateSnapshot

**Purpose**: All HA states needed to decide whether a state-change event should
update overrides.

**Fields**:

- `enabled_state`: effective enabled switch state, considering the changed entity.
- `code_state`: current PIN text state.
- `name_state`: current name text state.
- `use_date_range_state`: effective date-range switch state.
- `start_state`: current start datetime state or `None`.
- `end_state`: current end datetime state or `None`.
- `start_entity_id`: start entity id when date ranges are enabled.
- `end_entity_id`: end entity id when date ranges are enabled.

**Validation rules**:

- Disabled slots return early.
- Missing code or name states return early.
- Unreadable code or name values return early.
- A code with an empty readable name logs the current warning and returns early.

## NormalizedOverrideUpdate

**Purpose**: Final state-change payload sent to
`coordinator.update_event_overrides`.

**Fields**:

- `slot`: slot number.
- `slot_code`: normalized code string.
- `slot_name`: normalized or restored slot name string.
- `start_time`: parsed datetime or preserved/default local day.
- `end_time`: parsed datetime or preserved/default local day.

**Validation rules**:

- Existing override code/name/times are preserved during feedback paths exactly
  as today.
- Trim/prefix full-name restoration occurs only when the incoming guest name
  matches the expected `trim_name` result.
- Dispatch uses the same positional call style and does not request a
  reconciliation refresh.

## EventIdentity

**Purpose**: Public named tuple preserved from `util.py` for event matching.

**Fields**:

- `name`: slot name returned by `get_slot_name`.
- `start`: timezone-aware event start datetime.
- `end`: timezone-aware event end datetime.
- `uid`: normalized UID or `None`.

**Validation rules**:

- `get_event_identities` filters and normalizes events exactly as today.
- `get_event_names` calls the util-level `get_event_identities` wrapper so
  util-level event identity patches remain effective.
