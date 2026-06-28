<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Research: Decompose Event Overrides

## Decision: Keep `event_overrides.py` and add sibling helpers

Keep `custom_components/rental_control/event_overrides.py` as the public module
that defines and exports `EventOverrides`, `EventOverride`, and `ReserveResult`.
Add `custom_components/rental_control/event_overrides_helpers/` for internal
modules.

### Rationale

The current public import path is load-bearing. Production setup and config
helpers import `EventOverrides` with
`from ..event_overrides import EventOverrides`; tests import through
`custom_components.rental_control.event_overrides` and also import the module,
typed dict, named tuple, public class, and private regression seams. Keeping the
file as a shell preserves those real import paths without relying on
file-to-package replacement semantics. A sibling helper package follows the
approach already used by the decomposed coordinator and avoids exposing new
helper modules as public API.

### Alternatives considered

- Convert `event_overrides.py` into an `event_overrides/` package whose
  `__init__.py` re-exports `EventOverrides`. This would preserve the dotted
  import in normal Python imports, but it adds avoidable risk for tests that
  patch the module, direct imports of `EventOverride` and `ReserveResult`, and
  private seam access during a behavior-preserving refactor.
- Leave all logic in one file and only shorten functions. This would not address
  issue #575's goal of independently testable matching and application units and
  would make the mirror relationship harder to protect.

## Decision: Use one shared matcher for both mirror methods

Extract phase-level matcher helpers so `_find_overlapping_slot` and
`_slot_has_matching_event` call the same implementation of UID-positive
exact-name matching, exact-name strict-overlap matching, and trim-aware fallback.

### Rationale

The current source duplicates matching semantics in two directions. Both methods
must preserve phase ordering, strict interval overlap, UID-owner checks,
same-start bypass, preferred-slot selection, trim/prefix behavior, and restored
full-name mutation. Duplicating the extracted code would preserve today's shape
but not FR-008's safety goal. A shared matcher lets each wrapper preserve its
return shape while centralizing the risky phase semantics.

### Alternatives considered

- Keep two extracted matchers with copied logic. This would reduce file length
  but would keep the historic divergence risk that caused wrong-slot and
duplicate-code bugs.
- Make `_slot_has_matching_event` call `_find_overlapping_slot` directly without
  an explicit shared core. This is tempting but hides orientation-specific needs:
  `_slot_has_matching_event` must ask whether a specific stored slot wins for
  any event, while `_find_overlapping_slot` returns the winning slot for one
  incoming event. A shared core with explicit `MatchRequest` and `MatchResult`
  keeps that distinction visible.

## Decision: Extract pure decisions, keep side effects on shell

Move pure matching, cleanup, dispatch, preflight, result-reduction, suppression
payload, and diagnostics decisions into helpers. Keep locks, HA state reads,
Keymaster service helper calls, in-memory mutations, Store-neutral cache
updates, and refresh behavior in `EventOverrides`.

### Rationale

`EventOverrides` indirectly controls physical access. The safest decomposition
keeps side-effect ordering at the existing class boundary while making the
algorithmic decisions independently testable. Helpers can be tested with plain
snapshots and requests, while existing integration tests continue to exercise
real shell ordering.

### Alternatives considered

- Move whole async methods such as `_apply_clear` and `_apply_set` into helper
  modules. This would reduce the shell more quickly, but it would spread locks,
  HA reads, service calls, pending fences, and error mutation across modules.
- Create helper classes with references to the `EventOverrides` instance. This
  would reduce line counts but would preserve tight coupling and make behavior
  harder to reason about than pure request/result helpers.

## Decision: Use request dataclasses plus compatibility wrappers

Introduce `SlotReservationRequest` and `SlotUpdateRequest` for internal call
patterns. Convert the three 7-parameter public methods into thin wrappers using
a request-or-legacy normalizer and `*values`/`**legacy` while preserving real
call styles.

### Rationale

The active parameter-count gate counts the current signatures over the limit,
but the methods are compatibility surfaces. Real call-site analysis shows:

- `async_reserve_or_get_slot` is test-only/retired and uses all-keyword fields or
  four positionals plus `uid=`.
- `async_update` is production-consumed by `coordinator.py` with six positional
  values including prefix, by `util.py` with five reset positionals, and by tests
  with positional or keyword fields.
- `update` has no current production call in `custom_components/`; tests use five
  positionals and a few `prefix=` keyword cases.

A normalizer can preserve those forms exactly while new internal code passes a
single request object.

### Alternatives considered

- Break callers immediately to require request objects. This violates FR-017 and
  would turn the refactor into a public API change.
- Leave signatures over the parameter threshold. This violates FR-019 and would
  prevent removal of the complexity suppression.
- Use only `**kwargs`. This would preserve keyword tests but break production
  positional calls to `async_update` and positional test calls to the retired
  greedy shim.

## Decision: Split plan application by action family

Use `apply_dispatch.py`, `apply_clear.py`, `apply_set.py`, and
`apply_update.py` rather than one large `application.py`.

### Rationale

The heavy action methods have different safety invariants. Clears depend on
preflight reads and pending-clear state; sets depend on confirmed-empty checks,
tentative assignment, suppression payloads, and rollback; update-times and
overwrite depend on in-place date updates and clear-before-replace ordering.
Separate modules keep each file below the target size and let focused tests pin
each action family.

### Alternatives considered

- One application helper module. This would recreate a large, mixed-concern file
  and risk failing the 400-line gate.
- Push all action logic into reconciliation. Reconciliation computes desired
  actions but does not own Keymaster side effects or `EventOverrides` state; that
  would change package responsibilities and Store authority.

## Decision: Extract greedy cleanup as compatibility-only decisions

Move stale-slot cleanup decisions to `greedy_cleanup.py`, but keep
`async_check_overrides` as a retired shim on `EventOverrides`.

### Rationale

The coordinator now uses reconciliation plans for production stale-slot cleanup,
but existing tests and compatibility paths still exercise `async_check_overrides`.
Extracting its decisions protects eviction tolerance counters from regression
without re-authorizing the greedy path.

### Alternatives considered

- Delete or rewrite `async_check_overrides`. This violates FR-010 and existing
  regression coverage.
- Fold cleanup into the shared matcher only. Matching determines whether a slot
  has an event; cleanup also owns miss-count increments, immediate clears,
  malformed date windows, empty calendars, and beyond-boundary clears.

## Decision: Gate complexity directive removal on measurement

Remove the `complexity/file-too-large complexity/function-too-long` directive
only after measuring the final event-override feature area.

### Rationale

The current file has no hallucinated-import directive. Prior decomposition work
showed that complexity suppressions should be removed only after line count,
function length, and parameter-count thresholds are measured on the resulting
files.

### Alternatives considered

- Remove the directive at the start of implementation. This would make early
  extraction commits fail linting before the split is complete.
- Replace the directive on helper modules. This would defeat FR-020/FR-021 and
  hide new complexity debt.
