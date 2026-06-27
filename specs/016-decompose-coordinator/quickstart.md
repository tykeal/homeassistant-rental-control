<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Quickstart: Validate Coordinator Decomposition

This quickstart is for the future implementation stage. The PLAN PR is
documentation-only and must not include production code.

## 1. Confirm the public import boundary

```bash
uv run python - <<'PY'
from custom_components.rental_control.coordinator import RentalControlCoordinator
print(RentalControlCoordinator.__name__)
PY
```

Expected result: `RentalControlCoordinator`. Production callers must keep using
`from .coordinator import RentalControlCoordinator`; no caller should import from
`coordinator_helpers/`.

## 2. Run the existing parity oracle

Run the smallest coordinator-focused set first:

```bash
uv run pytest \
  tests/unit/test_coordinator.py \
  tests/unit/test_coordinator_buffer_update.py \
  tests/unit/test_event_overrides.py \
  tests/unit/test_keymaster_event_diagnostics.py \
  tests/unit/test_slot_reconciliation.py \
  tests/integration/test_refresh_cycle.py \
  tests/integration/test_slot_concurrency.py \
  -x -q
```

Then run the entity and check-in consumers that exercise FR-012 members:

```bash
uv run pytest \
  tests/unit/test_calendar.py \
  tests/unit/test_sensors.py \
  tests/unit/test_switch.py \
  tests/integration/test_checkin_tracking.py \
  -x -q
```

Escalate to the full suite before opening the implementation PR:

```bash
uv run pytest tests/
```

## 3. Add focused helper parity tests

Add tests that feed helper modules the same snapshots the current coordinator
uses and assert equivalent outputs:

- iCal parsing: RRULE skip, Smoobu extras, Blocked/Not available filtering,
  Honor Event Times, description times, manual override fallback, prefixing,
  timezone conversion, UID normalization, and sorted order.
- Reservation building: duplicate names, date-window matching, generated code,
  manual observed PIN preservation, aliases, display names, buffers, invalid
  reservation skip, and sensor lookup keys.
- Ghost reservations: missing-count increments, pending-set to pending-clear,
  physical-name mismatch fencing, invalid date skips, fingerprint history, and
  raw-PIN redaction.
- Slot observation: missing, unreadable, empty, occupied, phantom, date-range,
  enabled, last-error, and actual-state diagnostic snapshots.
- Keymaster bootstrap/adoption: partially reset forced clears, adopted
  placeholders, skipped unreadable states, cache-only Store adoption, and
  persisted mapping reload.
- Check-in protection: checked-in, checked-out, duplicate-name exact matching,
  missing active physical stay synthesis, and restore-deferral scenarios.
- Compatibility wrappers: `_find_observed_slot_by_name` accepts the current
  three-argument call and query-object call; `update_event_overrides` accepts the
  current util.py positional call, current keyword tests, and new
  `EventOverrideUpdate` input.

## 4. Validate behavior and side-effect order

For representative refresh fixtures, compare before/after serialized values for:

- `CalendarEvent` lists and `coordinator.event`;
- observed `ManagedSlot` lists and `EventOverrides` actual-state cache;
- regular and ghost `Reservation` lists;
- `DesiredPlan` selected slots, overflow, actions, diagnostics, and validation
  warnings;
- `EventOverrides.async_apply_plan()` operation results and service-call order;
- Store mappings, `latest_plan`, `latest_overflow`,
  `latest_reconciliation_diagnostics`, and `keymaster_event_diagnostics`;
- `async_request_refresh()`, `async_save_slot_store()`, and sensor
  `async_write_ha_state()` call counts and ordering.

No implementation test should bless new business behavior. If output differs,
fix the extraction unless a separate accepted issue explicitly changes behavior.

## 5. Check complexity before directive removal

Before removing the complexity `aislop` directive, measure the coordinator
feature area:

```bash
uv run python - <<'PY'
from pathlib import Path
for path in [Path('custom_components/rental_control/coordinator.py'), *sorted(Path('custom_components/rental_control/coordinator_helpers').glob('*.py'))]:
    if path.exists():
        print(f'{path}: {len(path.read_text().splitlines())} lines')
PY
```

Confirm each coordinator-related file is below 400 lines, every project-owned
function is below 80 lines, and every project-owned parameter list has no more
than six parameters. Remove only:

```python
# aislop-ignore-file complexity/file-too-large complexity/function-too-long -- Existing module size is outside this emergency fix scope.
```

Keep:

```python
# aislop-ignore-file ai-slop/hallucinated-import -- Provided by Home Assistant runtime.
```

## 6. Run linting before commit

```bash
uv run ruff check custom_components/ tests/
```

If hooks modify files during commit, stage the changes and retry the same commit.
Do not bypass hooks.
