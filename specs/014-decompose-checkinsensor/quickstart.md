<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Quickstart: Check-in Sensor Decomposition Validation

## Scope

This guide validates the implementation stage for spec
`014-decompose-checkinsensor`. The refactor is behavior-preserving: it must not
add states, events, services, configuration options, persistence fields, or new
timing rules. Existing check-in behavior tests are the parity oracle; new tests
only make extracted decisions easier to verify.

## Developer Setup

```bash
cd path/to/homeassistant-rental-control
uv sync
```

Use targeted tests first, then escalate only if failures indicate broader
breakage.

## Existing Parity Tests

Run the current check-in sensor coverage unchanged:

```bash
uv run pytest tests/unit/test_checkin_sensor.py   tests/integration/test_checkin_tracking.py -q
```

These tests already cover:

- basic four-state transitions and attributes;
- check-in and checkout HA event payloads;
- same-day turnover midpoint linger;
- different-day midnight and follow-up awaiting timers;
- no-follow-on cleaning-window linger;
- restore of checked-in, awaiting, checked-out, no-reservation, and unknown
  states;
- auto-checkout rescheduling when event end changes;
- Keymaster monitoring and unlock-triggered check-in;
- child lock name propagation and persistence;
- manual checkout and early-expiry behavior;
- debug `set_state` clearing tracked fields and timers;
- event lookup by identity after coordinator reordering;
- self-healing of far-future checked-in state.

Also run the call-site coverage that constructs or observes the sensor outside
the dedicated files:

```bash
uv run pytest tests/unit/test_keymaster_event_diagnostics.py \
  tests/unit/test_sensors.py \
  tests/integration/test_full_setup.py -q
```

## New Focused Unit Tests

Add extracted-helper tests that verify the same behavior with explicit inputs.
Suggested files:

```bash
uv run pytest tests/unit/test_checkin_decisions.py \
  tests/unit/test_checkin_restore.py \
  tests/unit/test_checkin_timers.py \
  tests/unit/test_checkin_persistence.py -q
```

### Coordinator decision cases

1. `no_reservation` with no event returns write-only/no-op.
2. `no_reservation` with a future event returns awaiting plus auto-check-in
   timer intent.
3. `awaiting_checkin` refreshes mutable tracked end/slot fields for the same
   event.
4. `awaiting_checkin` switches to a more relevant earlier event but not to the
   just-checked-out event key.
5. `awaiting_checkin` auto-checks in when start passed and monitoring is off;
   it stays awaiting when monitoring is on.
6. `checked_in` reschedules auto-checkout when the tracked end changes.
7. `checked_in` forces checkout when the event ended or stored fallback end has
   passed.
8. `checked_in` far-future self-healing produces checkout first and optional
   awaiting second.
9. `checked_in` with transient missing event preserves state and emits the same
   warning/debug behavior.
10. `checked_out` keeps an existing same-follow-on timer, recomputes changed or
    removed follow-ons, and recomputes when no timer is active.

### Restore reconciliation cases

1. Restored `checked_in` with ended event silently becomes `checked_out` and
   computes linger.
2. Restored `checked_in` with far-future start silently checks out and stores the
   same checked-out event key.
3. Restored valid `checked_in` reschedules auto-checkout or clears target when no
   end is known.
4. Restored `awaiting_checkin` with past start and monitoring off silently checks
   in, and silently checks out too if the end also passed.
5. Restored `awaiting_checkin` with monitoring on reschedules/stays awaiting.
6. Restored `checked_out` with a new relevant event transitions to awaiting.
7. Restored `checked_out` with expired linger resets to no reservation.
8. Restored `no_reservation` recreates a future FR-006c follow-up timer or clears
   stale follow-up data.
9. Unknown/corrupted restored state resets through the same no-reservation path.

### Timer cases

1. Scheduling any timer with an active handle cancels the old handle before
   storing the new one.
2. Auto-check-in, auto-checkout, same-day linger, different-day linger, cleaning
   window, and follow-up timers each expose the same target time as before.
3. Callback entry clears the active handle before state guards.
4. Stale callbacks in the wrong state do nothing.
5. Debug `async_set_state`, no-reservation transition, manual checkout, and
   entity removal cancel pending timers.

### Persistence cases

1. `CheckinExtraStoredData.as_dict()` emits the exact existing key set.
2. `from_dict(as_dict())` round-trips all fields, including `checkin_lock_name`
   and `next_event_start_day`.
3. Missing optional fields restore as `None` and missing `state` restores as
   `no_reservation`.
4. Invalid datetime strings log warnings and restore as `None`.
5. The new snapshot-based initializer stays at or below six parameters.

## Performance and Hot-Path Checks

Review or test the decomposed coordinator update to confirm it:

- does not await or call HA services;
- does not call `coordinator.async_request_refresh()` or fetch new data;
- performs event scans only over existing `coordinator.data`;
- writes HA state at the same points as the original source;
- keeps bus-event emission inside the same transition paths.

## Final Validation Before Implementation PR Merge

```bash
uv run pytest tests/ -x -q
uv run ruff check custom_components/ tests/
uv run pre-commit run --all-files
```

All tests and hooks must pass without `--no-verify`, `--no-gpg-sign`, or other
bypasses. Any behavior difference from the current source is a regression unless
a later accepted issue explicitly changes the check-in sensor contract.
