<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Quickstart: Event Overrides Decomposition Parity

This quickstart is for the later IMPLEMENT stage. The PLAN stage is docs-only
and does not modify production code.

## 1. Establish the existing behavior oracle

Run the current event-override-focused tests before extraction:

```bash
uv run pytest tests/unit/test_event_overrides.py tests/integration/test_slot_concurrency.py -q
```

Run the current callers that exercise the production compatibility surface:

```bash
uv run pytest \
  tests/unit/test_coordinator.py \
  tests/unit/test_sensors.py \
  tests/integration/test_refresh_cycle.py \
  -q
```

## 2. Add focused matcher parity tests before moving logic

Add tests around the existing `EventOverrides` shell, then keep them passing as
`event_overrides_helpers.matcher` is introduced. Cover all three phases and both
mirror methods:

- UID-positive exact-name match wins before overlap or trim fallback.
- Date-shifted UID-positive match does not require overlap.
- Exact-name matching uses strict overlap only:
  `start_a < end_b AND start_b < end_a`.
- Same-start UID bypass accepts only same UTC start times, preserves exact UID
  owner precedence, and keeps preferred-slot tie-breaking.
- `exclude_slot` prevents async-update duplicate checks from matching the slot
  being written.
- Trim-aware fallback uses `trim_name`, `trim_names`, `max_name_length`,
  `event_prefix`, and `prefix_length` exactly.
- Prefix-stripped and trimmed stored names restore the longer full name only when
  current code restores it.
- Duplicate names, missing UIDs, duplicate UID owners, and no-match cases return
  the same slot or no slot as current source.
- `_find_overlapping_slot` and `_slot_has_matching_event` use the same phase
  semantics: for every event fixture, the slot selected by the former is the
  only slot considered matching by the latter unless current source says no
  target slot owns that event.

Suggested targeted command after adding tests:

```bash
uv run pytest \
  tests/unit/test_event_overrides.py \
  tests/unit/test_event_overrides_matcher.py \
  -q
```

## 3. Add plan-application parity tests

Pin `async_apply_plan`, `_apply_clear`, `_apply_set`, `_apply_update_times`, and
`_apply_overwrite_manual_change` before moving decisions into helpers. Cover:

- `NOOP` and `BLOCKED` actions skip without operation results.
- `CLEAR`, `RETRY_CLEAR`, and `RESET` preserve warning reasons, preflight-read
  behavior, pending fences, pending-clear state, confirmed-empty release,
  failed clear errors, lingering state errors, and stale-token handling.
- `SET` and `ASSIGN` require confirmed-empty physical state, tentatively assign
  overrides at the same point, suppress the same feedback entities, rollback on
  failure, and keep tentative state on unconfirmed results as today.
- `UPDATE_TIMES` preserves service calls, suppression markers, and cached
  buffered start/end updates on confirmed results.
- `OVERWRITE_MANUAL_CHANGE` and `UPDATE_IN_PLACE` preserve drift logging without
  raw PINs, clear-before-replace ordering, skipped replacement when clear is not
  confirmed, and replacement set semantics.
- `reconciliation_active` becomes true and false at the same points, and
  diagnostics snapshots are updated in `finally`.

Suggested targeted command:

```bash
uv run pytest \
  tests/unit/test_event_overrides.py \
  tests/unit/test_event_overrides_apply.py \
  -q
```

## 4. Pin wrapper compatibility for parameter reductions

Before changing signatures, add tests for the real call styles:

- `async_reserve_or_get_slot(slot_name=..., slot_code=..., start_time=...,
  end_time=..., uid=...)`
- `async_reserve_or_get_slot(name, code, start, end, uid=...)`
- `async_update(slot, code, name, start, end, prefix)` as used by
  `coordinator.py`
- `async_update(slot, "", "", start, end)` as used by `util.py`
- `async_update(slot=..., slot_code=..., slot_name=..., start_time=...,
  end_time=...)` as used by tests
- `update(slot, code, name, start, end)` and `update(..., prefix="Rental")`

After introducing `SlotReservationRequest` and `SlotUpdateRequest`, add direct
request-object tests too. Unknown legacy keywords should fail fast in focused
unit tests, but all existing production and test call styles must continue to
work unchanged.

## 5. Validate greedy cleanup and eviction tolerance

Run existing stale-slot tests and add focused helper tests for:

- matched slots reset `_slot_miss_counts`;
- future missing slots increment counts and clear only at
  `SLOT_MISS_THRESHOLD`;
- past, malformed, empty-calendar, and beyond-boundary slots clear immediately
  when current source clears immediately;
- unconfirmed, failed, or lingering clear results preserve the occupied slot;
- successful retired-greedy clears call `__assign_next_slot()` exactly as today.

Suggested targeted command:

```bash
uv run pytest tests/unit/test_event_overrides.py -q
```

## 6. Validate public compatibility surface

Run import and caller compatibility coverage after the shell delegates to helper
modules:

```bash
uv run pytest \
  tests/unit/test_event_overrides.py \
  tests/unit/test_coordinator.py \
  tests/unit/test_sensors.py \
  tests/integration/test_refresh_cycle.py \
  tests/integration/test_slot_concurrency.py \
  -q
```

Confirm all FR-017 members remain on `EventOverrides` and all FR-018 private
regression seams used by tests still behave the same.

## 7. Measure complexity before removing the directive

Only after extraction is complete, measure event-override files and functions:

```bash
uv run python - <<'PY'
import ast
from pathlib import Path
for path in [Path('custom_components/rental_control/event_overrides.py'), *Path('custom_components/rental_control/event_overrides_helpers').glob('*.py')]:
    lines = path.read_text().splitlines()
    print(f'{path}: {len(lines)} lines')
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, 'end_lineno', node.lineno)
            params = (
                len(node.args.posonlyargs)
                + len(node.args.args)
                + len(node.args.kwonlyargs)
                + (1 if node.args.vararg else 0)
                + (1 if node.args.kwarg else 0)
            )
            if end - node.lineno + 1 > 80 or params > 6:
                print(f'  {node.name}: lines={end - node.lineno + 1} params={params}')
PY
```

Remove only the event-overrides complexity directive after every related file is
below 400 lines, every project-owned function is 80 lines or fewer, and every
project-owned parameter list is no more than six parameters. There is no
hallucinated-import directive on `event_overrides.py`.

## 8. Run final validation

Run targeted tests, lint, then the full suite if targeted tests pass:

```bash
uv run pytest \
  tests/unit/test_event_overrides.py \
  tests/integration/test_slot_concurrency.py \
  tests/integration/test_refresh_cycle.py \
  -q
uv run ruff check custom_components/ tests/
uv run pytest tests/ -q
```

Do not merge implementation work unless existing behavior tests pass unchanged,
new helper parity tests pass, complexity checks pass without the old directive,
and no production caller had to change its `EventOverrides` call behavior.
