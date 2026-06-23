<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Quickstart: Stateless Slot Reconciliation Validation

## Scope

This guide validates the implementation stage for spec
`013-stateless-reconciliation`. It is not a task list and does not implement the
feature. Validation must prove that every refresh reconciles from physical
Keymaster state plus current calendar data, stable slot-name matching prevents
duplicates across reservation changes, confirmed-reset-before-reapply holds
without persisted fences, and the Store is cache-only.

## Developer Setup

```bash
cd /home/tykeal/repos/personal/homeassistant/rental-control
uv sync
```

Run targeted tests first, then escalate only if failures indicate broader
breakage.

```bash
uv run pytest tests/unit/test_slot_reconciliation.py \
  tests/unit/test_event_overrides.py \
  tests/unit/test_util.py -q
uv run pytest tests/integration/test_refresh_cycle.py \
  tests/integration/test_slot_concurrency.py -q
uv run ruff check custom_components/ tests/
```

## Pure Planner Unit Tests

Create or migrate `tests/unit/test_slot_reconciliation.py` around the stateless
planner inputs and outputs.

1. **Already correct no-op**: physical slot name, PIN, and buffered dates match
   one desired reservation; action is `noop` and no service call is planned.
2. **Length increase**: physical slot name matches a desired reservation whose
   end time moved later; planner emits `update_in_place` for the same slot and
   no second slot is selected for that reservation.
3. **Length decrease**: same as above with earlier end time; same slot is used.
4. **Full date shift with date-based code change**: physical name matches, old
   generated code differs from the new generated code, planner emits
   `update_in_place(replace_code)` for the same slot, and the desired
   reservation appears in exactly one slot.
5. **Same-guest back-to-back**: adjacent reservations with the same stable name
   are paired by start-time order and do not collapse into one slot.
6. **Duplicate guest names**: two concurrent upcoming reservations with the same
   display/stable name are matched by start-time order; each selected
   reservation appears in exactly one managed slot.
7. **Duplicate names plus date shift**: both duplicate-name reservations change
   dates in one refresh; physical slots pair to desired reservations by ordered
   stable-name group, update in place, and no extra slot is allocated. Add an
   ambiguous reorder/trim-collision variant and assert the affected writes are
   blocked with diagnostics rather than guessed.
8. **Soonest-N overflow**: with more eligible reservations than managed slots,
   selected non-active reservations are the earliest starts after protected
   active guests are counted.
9. **Farther reservation dropout**: when a nearer reservation enters capacity,
   the farthest unprotected physical occupant resets, and the nearer
   reservation is assigned only after an empty slot is confirmed.
10. **Duplicate physical occupant**: same desired reservation appears in two
    physical slots by name; one canonical slot remains matched and the
    non-canonical duplicate resets through confirmed clear.
11. **Unknown/unavailable conservative**: `unknown`, blank, and `None` count as
    cleared text states; `unavailable` blocks assignment.
12. **Unmanaged slot ignored**: a populated slot outside the configured managed
    range never receives an action.

## Confirmed Reset and Apply Tests

Use `tests/unit/test_event_overrides.py` and `tests/unit/test_util.py` to pin
service-operation safety.

1. `async_fire_clear_code()` returns confirmed only when both physical name and
   PIN are cleared; lingering name/PIN leaves the action unconfirmed.
2. `async_fire_clear_code()` treats `unavailable` name or PIN as unconfirmed,
   not empty.
3. `update_in_place(replace_code)` re-reads the physical slot, calls clear
   first, re-reads/sets the same reservation into the same slot only after the
   clear result is confirmed, and never writes a replacement PIN after an
   unconfirmed clear.
4. A clear that remains physically occupied is not fenced by Store; the next
   planner run sees the slot still occupied and retries or blocks from physical
   state.
5. A physically empty slot previously mentioned in a legacy pending-clear cache
   is assignable because Store is non-authoritative.
6. Set confirmation reuses `_async_wait_for_expected_name()` and returns
   unconfirmed when the bounded wait expires.
7. Callback suppression ignores coordinator-originated name/PIN/date feedback
   and does not launch reconciliation.

## Store Non-Authoritative Tests

Create tests that run the same physical/calendar scenario with cache present,
cache missing, cache stale, cache contradictory, and cache corrupt. The planned
actions must be identical.

1. **Deleted Store cold start**: existing coded Keymaster slots are recognized by
   physical stable name and reconciled in place without adoption.
2. **First upgrade with legacy 3.5.x Store**: legacy `status`, `slot`,
   `pending_clear_since`, `operation_id`, `missing_count`, and `blocked_slots`
   fields are ignored for correctness; no working slot is wiped merely because
   Store disagrees.
3. **Contradictory Store**: cache says slot 10 belongs to Alice while physical
   slot 10 is Bob and calendar wants Bob; Bob wins by physical name.
4. **Stale pending clear**: cache says slot is pending clear but physical name
   and PIN are empty; planner may assign an unassigned desired reservation.
5. **Cache deletion mid-run**: after a successful refresh, delete the Store file
   or make `async_load()` return `None`; the next refresh with unchanged
   physical slots and calendar emits the same no-op/update/reset/assign actions.
6. **Cache write failure**: simulate Store save failure; physical reconciliation
   still completes and later refresh behavior is unchanged.

## Preserved Semantics Tests

1. **Manual door-code override**: a matched physical slot whose PIN does not
   equal the generated code for its observed dates is treated as manual and
   preserved in the desired reservation; raw PIN is not persisted or logged.
2. **Generated date-based code replacement**: when the observed PIN equals the
   previously generated date-based code and dates shift with
   `should_update_code` enabled, the old generated code is replaced in place.
3. **Manual time overrides**: when Honor Event Times is off, existing/manual
   time-of-day overrides still determine the programmed window.
4. **Honor Event Times**: timed calendar events use PMS event times; all-day
   events use description times or configured/default fallback as today.
5. **Buffers**: before/after lock-code buffers produce the same Keymaster
   date-range values as the existing `apply_buffer()` helper.
6. **Trimming and prefixing**: matching accepts full, prefixed, trimmed, and
   prefixed-trimmed physical names, and writes the configured display name.
7. **Check-in tracking**: checked-in guests are protected and checked-out guests
   can leave the should-be set; sensor attributes remain unchanged.
8. **`event_N` sensors**: sensors expose the same calendar attributes and read
   `slot_number`/`slot_code` from the latest stateless plan without invoking
   `async_reserve_or_get_slot()`, set, clear, or update services. Include a
   date-shift case proving the sensor's existing event fingerprint lookup is
   bridged to the refresh-local desired ID.
9. **Manual time override from physical state**: a matched physical slot with
   manually adjusted Keymaster dates feeds the desired access window after
   reversing buffers when Honor Event Times does not override it.
10. **Check-in unlock validation**: unlock validation reads latest plan/observed
    slot ownership, not deleted override maps, and still rejects unlocks from a
    different guest's slot.

## Integration Scenarios

Use `tests/integration/test_refresh_cycle.py` and
`tests/integration/test_slot_concurrency.py` for end-to-end refresh behavior.

### Scenario A: reservation length increase/decrease

- Seed a managed physical slot with a reservation by name, generated PIN, and
  dates.
- Change only the checkout date later, run refresh, confirm the same slot is
  updated and no duplicate exists.
- Change checkout earlier, run refresh, confirm the same slot is still the only
  physical assignment.

### Scenario B: full date shift and code change

- Use `date_based` code generation.
- Seed a physical slot with old dates/code for a reservation.
- Move both start and end so the generated code changes.
- Run refresh with clear and set confirmations.
- Assert the old physical slot is cleared, confirmed empty, and set with the new
  code/name/dates; no other slot receives that reservation.

### Scenario C: duplicate names and rebooking

- Seed two same-name reservations in two physical slots.
- Shift one reservation's dates and add a back-to-back same-guest stay.
- Run refresh and assert start-time ordered matching leaves each selected stay
  in exactly one slot with no duplicate assignment.

### Scenario D: soonest-N overflow and active protection

- Fill all managed slots with farther-future reservations.
- Mark one current guest checked in through the check-in sensor.
- Add a nearer upcoming reservation.
- Run refresh. Assert the active guest remains, the nearer reservation enters
  after a confirmed reset, and the farthest unprotected reservation overflows.

### Scenario E: confirmed-reset-before-reapply

- Require a reset before assigning a new reservation.
- Force reset confirmation to lag by leaving name or PIN populated.
- Run refresh and assert no replacement PIN is written.
- Change physical state to empty and run refresh again; assignment may proceed.

### Scenario F: physical-empty self-heal

- Seed a managed slot with blank/`unknown`/`None` name and PIN while legacy cache
  claims it is occupied or pending clear.
- Run refresh and assert the slot is treated as free and can receive an
  unassigned selected reservation.

### Scenario G: manual drift correction

- Manually edit a managed slot name, PIN, or dates away from its desired
  reservation.
- Run refresh and assert the planner either restores the matched desired state
  in place or resets an unrelated/stale occupant, with diagnostics identifying
  the drifted fields and no raw PIN values.

### Scenario H: callback re-entrancy

- Start an apply operation and emit Keymaster name/PIN/date state changes while
  it is in progress.
- Assert callbacks update in-memory feedback or are suppressed when
  coordinator-originated, call any compatibility update path with
  `request_refresh=False`, and do not start nested reconciliation.
- Complete the operation and verify the next normal refresh observes physical
  truth.

## Manual Verification

In a Home Assistant development instance with a test Keymaster lock:

1. Configure Rental Control with two managed slots.
2. Program two upcoming reservations and confirm both slots match their names.
3. Extend one stay, wait for refresh, and confirm the same slot updates without
   a duplicate.
4. Shift the stay dates so the date-based code changes; confirm the old slot is
   cleared then reprogrammed in place only after empty confirmation.
5. Delete the Rental Control slot-mapping Store file or disable Store loading,
   restart/reload, and confirm existing physical slots reconcile by name.
6. Mark a guest checked in, add nearer reservations, and confirm active guest
   protection.
7. Manually edit a slot and confirm drift diagnostics plus self-healing.

## Expected Final Validation

Before merging the implementation PR, run:

```bash
uv run pytest tests/ -x -q
uv run ruff check custom_components/ tests/
pre-commit run --all-files
```

All tests and hooks must pass without `--no-verify`, `--no-gpg-sign`, or other
bypasses. The final implementation must prove deleting the Store mid-run causes
no behavior change.
