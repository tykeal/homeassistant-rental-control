<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Quickstart: Slot Reconciliation Validation

## Scope

This guide validates the implementation stage for spec
`012-slot-reconciliation`. It is not a task list and does not implement the
feature. The goal is to prove the coordinator now reconciles RC-managed
Keymaster slots to one desired plan on every refresh.

## Developer Setup

```bash
cd /home/tykeal/repos/personal/homeassistant/rental-control
uv sync
```

Use targeted tests first, then escalate only if failures suggest wider impact.

```bash
uv run pytest tests/unit/test_slot_reconciliation.py \
  tests/unit/test_event_overrides.py \
  tests/unit/test_util.py -q
uv run pytest tests/integration/test_refresh_cycle.py \
  tests/integration/test_slot_concurrency.py -q
uv run ruff check custom_components/ tests/
```

## Unit Test Coverage

### Desired-plan selection

Create pure tests in `tests/unit/test_slot_reconciliation.py` for:

1. **Soonest-N overflow**: with `max_events=3` and five eligible reservations,
   selected identities are the three earliest start times.
2. **No farther before nearer**: a persisted farther reservation is replaced by
   a newly discovered nearer reservation once the farther slot can be cleared.
3. **Active protection**: a checked-in guest remains selected and retains its
   current slot even when start-time ordering alone would evict it.
4. **Churn minimization**: selected reservations keep persisted slots when no
   protected or pending-clear conflict exists.
5. **Equal-start determinism**: equal starts sort by identity key.
6. **Overflow diagnostics**: unselected reservations include rank and reason.

### Identity and Store migration

Create Store/migration tests for:

1. Exact stable fingerprint preserves mapping after restart.
2. UID changes but name/start/end are stable; mapping is retained.
3. UID matches and dates shift; mapping updates in place and triggers the
   existing code/times update decision.
4. UID and dates both change, but exactly one stored mapping matches by
   fingerprint history, booking aliases, normalized name, non-overlap ordering,
   and actual-slot continuity; mapping updates in place.
5. UID and dates both change with two compatible candidates; no collapse occurs
   and diagnostics report ambiguity.
6. No Store on first upgrade with matching populated Keymaster slots; mappings
   are adopted without clearing working slots.
7. First-upgrade adoption matches prefixed/trimmed Keymaster names and buffered
   Keymaster dates back to untrimmed reservation identity before deciding a
   working slot is stale.
8. No Store on first upgrade with phantom name-only slot; slot becomes
   pending-clear and is not reused until confirmed clear.
9. Ambiguous populated slots on first upgrade are blocked/diagnostic rather than
   cleared blindly.
10. Store pending-clear survives restart and keeps the slot unavailable.

### Confirmed-clear safety

Create tests in `tests/unit/test_event_overrides.py` and
`tests/unit/test_util.py` for:

1. `async_fire_clear_code()` returns an explicit result indicating confirmed
   clear, unconfirmed clear, or failed service call.
2. Reset service failure leaves the slot `pending_clear`/blocked and preserves
   previous identity metadata.
3. Partial reset with lingering name is force-cleared; if verification still
   observes a name or PIN, the slot remains blocked.
4. A pending-clear slot is excluded from desired assignment even when a nearer
   reservation needs capacity.
5. Later successful verification moves the slot to `free` and allows assignment
   on the next plan.

### Duplicate, phantom, and manual drift

Seed actual-state snapshots and verify:

1. Duplicate actual assignments collapse to one canonical slot.
2. Non-canonical duplicate slots are cleared and not reused until confirmed.
3. Farther-future actual assignment with a nearer unassigned reservation
   produces a clear/set diff toward the nearer reservation.
4. Phantom name-only state is classified separately from a usable occupied slot.
5. Manual name/code/date edits in managed slots are logged and overwritten.
6. Slots outside the configured RC-managed range are ignored.
7. `caplog` captures required FR-017 log entries for duplicate collapse,
   overflow decisions, blocked clear failures, phantom recovery, stale
   correction, and manual overwrite.

### Sensor read-only behavior

Update `tests/unit/test_sensors.py` so `RentalControlCalSensor`:

1. Does not call `async_reserve_or_get_slot()`, `async_fire_set_code()`,
   `async_fire_clear_code()`, or `async_fire_update_times()` from
   `_handle_coordinator_update()`.
2. Reads `slot_number` and displayed `slot_code` from the coordinator's latest
   desired/reconciled state.
3. Still exposes summary, start, end, ETA, parsed attributes, UID, slot name,
   and no-reservation attributes as before.

### Preserved semantics regressions

Add or extend tests proving:

1. Slot-name trimming still applies only to Keymaster display names.
2. Lock-code before/after buffers still produce the same buffered dates.
3. Honor-PMS-times behavior still follows timed events, description times,
   stored override fallback, and configured default times.
4. Date-based code generation and `should_update_code` still decide whether a
   date shift clears/regenerates a code or only updates times.
5. Check-in tracking states and checkout behavior still match existing tests.

## Integration Scenarios

Use `tests/integration/test_refresh_cycle.py` and
`tests/integration/test_slot_concurrency.py` to simulate complete refreshes.

### Scenario A: full slots plus nearer new reservation

- Seed all managed slots with later reservations.
- Add a nearer reservation to the calendar.
- Run one refresh with successful clear/set confirmations.
- Assert the nearer reservation is assigned and the farthest unprotected
  reservation is overflow.

### Scenario B: active guest protected

- Seed a checked-in reservation in one managed slot.
- Add enough earlier-starting upcoming reservations to exceed capacity.
- Run reconciliation.
- Assert the checked-in reservation keeps its slot and remaining slots contain
  the soonest non-active reservations.

### Scenario C: corrupted state self-healing

Run separate parameterized cases for:

- duplicate reservation in two slots;
- phantom name-only slot;
- stale expired assignment;
- mis-assigned farther reservation;
- manual edit to a managed slot.

Assert refreshes converge without restart/reload/clear-all, except for slots
that remain blocked by unconfirmed physical operations.

### Scenario D: no double assignment on clear failure

- Require a clear before assigning a new reservation.
- Make the clear service fail or make post-clear verification see a lingering
  name/PIN.
- Assert the old slot stays `pending_clear`/blocked, no new reservation receives
  that slot, and diagnostics include the blocked reason.
- Make the later clear verification succeed and assert the next refresh can use
  the slot.

### Scenario E: restart persistence and UID churn

- Assign reservations and save Store state.
- Recreate coordinator/EventOverrides to simulate restart.
- Deliver the same reservations with changed UIDs.
- Assert mappings are preserved, existing slots are not wiped, and diagnostics
  show stable identity matches.

### Scenario F: two-cycle feed-miss tolerance

- Assign a reservation to a slot.
- Omit it from the feed for refresh 1 and refresh 2.
- Assert it remains assigned and missing count increments.
- Omit it for refresh 3.
- Assert it becomes clearable unless protected.
- Repeat with reappearance before refresh 3 and assert missing count resets.

### Scenario G: diagnostics completeness

For a plan containing a matched slot, an overflow reservation, a manual drift,
and a pending clear, capture diagnostics and assert each RC-managed slot includes
all of:

- desired reservation identity or `None`;
- actual Keymaster state classification;
- pending correction or blocked reason;
- overflow status where applicable.

### Scenario H: callback re-entrancy fencing

- Start a clear or set operation and assert Store is fenced with the operation
  token before the service call is awaited.
- Fire Keymaster state-change callbacks while the operation is in progress.
- Assert callbacks only update observed state or dirty flags and do not launch
  reconciliation.
- Complete verification and assert the slot transitions only when the operation
  token still matches.

## Manual Verification

In a Home Assistant development instance with a test Keymaster lock:

1. Configure Rental Control with a small managed range, such as two slots.
2. Seed three upcoming test reservations and confirm only the soonest two are
   programmed.
3. Add a nearer reservation and confirm the farthest unprotected reservation is
   cleared before replacement.
4. Put the current guest into `checked_in` state and confirm they are not evicted
   while active.
5. Manually edit a managed slot name or date, then wait for refresh and confirm
   Rental Control logs the overwrite and restores the desired state.
6. Simulate a clear failure by making the reset entity unavailable; confirm the
   slot remains blocked and diagnostics explain why.
7. Restart Home Assistant, alter calendar UIDs if possible, and confirm mappings
   persist without clearing working slots.

## Expected Final Validation

Before merging the implementation PR, run:

```bash
uv run pytest tests/ -x -q
uv run ruff check custom_components/ tests/
pre-commit run --all-files
```

All tests and hooks must pass without `--no-verify` or other bypasses.
