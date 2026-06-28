<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Implementation Plan: Decompose Event Overrides

**Feature**: `017-decompose-event-overrides` | **Planning Branch**:
`017-decompose-event-overrides-plan` | **Date**: 2026-06-28 |
**Spec**: [spec.md](spec.md)
**Input**: Feature specification from
`specs/017-decompose-event-overrides/spec.md` and GitHub issue #575

## Summary

Decompose `custom_components/rental_control/event_overrides.py` without
changing Home Assistant-visible behavior. The current 1,864-line source is the
load-bearing contract: it owns the `EventOverrides` compatibility surface,
in-memory slot assignments, UID ownership, trim/prefix identity, the retired
greedy reservation path, stale-slot eviction tolerance, reconciliation plan
application, pending fences, pending-clear state, service-feedback suppression,
actual-state cache, retry/error tracking, and diagnostics snapshots.

The implementation will keep `event_overrides.py` as the public
`EventOverrides` engine shell and add a sibling internal package,
`custom_components/rental_control/event_overrides_helpers/`. This avoids import
risk for existing consumers that import
`custom_components.rental_control.event_overrides.EventOverrides` or use the
internal relative `from ..event_overrides import EventOverrides`, while allowing
pure matching, plan-application decisions,
trim/prefix helpers, slot bookkeeping, eviction decisions, and diagnostics
projection to move into focused modules. The refactor is acceptable only if
identical inputs produce the same selected slots, restored names, operation
results, service calls, side-effect ordering, cached state, diagnostics, retry
state, and compatibility helper results.

## Technical Context

**Language/Version**: Python >=3.14.2
**Primary Dependencies**: Home Assistant runtime >=2026.4.0 per `hacs.json`;
dev/test dependency `homeassistant>=2026.6.0` per `pyproject.toml`;
`pytest-homeassistant-custom-component`, `icalendar>=7.0.0`, and
`x-wr-timezone>=2.0.0`
**Storage**: Home Assistant `Store` mappings remain cache-only inputs loaded
into `EventOverrides`; no new storage and no new Store authority over physical
Keymaster state or calendar reservations
**Testing**: `uv run pytest tests/`; targeted event-override coverage in
`tests/unit/test_event_overrides.py`, `tests/integration/test_slot_concurrency.py`,
`tests/integration/test_refresh_cycle.py`, `tests/unit/test_coordinator.py`,
`tests/unit/test_sensors.py`, and utility/coordinator coverage that exercises
`verify_slot_ownership`, `async_update`, and `async_apply_plan`; ruff via
`uv run ruff check custom_components/ tests/`; pre-commit hooks for reuse,
ruff, mypy, interrogate, yamllint, actionlint, and gitlint
**Target Platform**: Home Assistant custom integration on the HA asyncio event
loop for Linux, HA OS, Docker, and HACS-managed installs
**Project Type**: Single Home Assistant custom integration
**Performance Goals**: Matching and cleanup remain in-memory scans over the
same override slots and current calendar identities. Plan application performs
the same Keymaster service calls, fresh HA state reads, Store-neutral cache
updates, and diagnostics projection as today with no extra coordinator refreshes
or blocking I/O.
**Constraints**: Documentation-only PLAN PR; no production code. Runtime
implementation must preserve the three-phase matching order, strict interval
overlap, same-start UID bypass, trim/prefix behavior, eviction tolerance,
reconciliation plan dispatch, clear preflight safety, confirmed-empty set
safety, update-times/overwrite ordering, public `EventOverrides` import path,
and all FR-017/FR-018 compatibility seams.
**Scale/Scope**: One 1,864-line module becomes a small public shell plus an
internal helper package. Current measured complexity debt includes
`_find_overlapping_slot` (166 lines), `async_check_overrides` (151),
`_slot_has_matching_event` (113), `async_apply_plan` (96), `_apply_set` (94),
`_apply_clear` (82), and the 7-parameter methods
`async_reserve_or_get_slot`, `async_update`, and `update`. The implementation
target is all event-override files below 400 lines, project-owned functions
80 lines or fewer, and project-owned parameter lists no more than six parameters.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I: Code Quality & Testing | PASS | Plan requires existing event override, slot concurrency, refresh-cycle, coordinator, sensor, and utility tests to pass unchanged and adds focused parity tests for extracted matching and plan application. |
| II: Atomic Commit Discipline | PASS | This PR is one docs-only PLAN commit. Future implementation can split models, matcher extraction, application helpers, shell wiring, and tests into atomic commits. |
| III: Licensing & Attribution | PASS | New markdown artifacts include SPDX headers. Future Python helper modules must include project SPDX headers. |
| IV: Pre-Commit Integrity | PASS | No hook bypass is planned. Quickstart defines local validation before implementation merge. |
| V: Agent Co-Authorship & DCO | PASS | The PLAN commit uses `git commit -s` and the requested AI co-author trailer. |
| VI: User Experience Consistency | PASS | Public imports, class members, matching results, service-call behavior, diagnostics, and caller call styles are explicitly preserved. |
| VII: Performance Requirements | PASS | Extracted helpers are pure in-memory decision logic; HA reads, service calls, state writes, and refreshes stay on the shell with no new work. |

**Gate result: PASS** — no violations. Proceeding to Phase 0.

## Project Structure

### Documentation (this feature)

```text
specs/017-decompose-event-overrides/
├── plan.md                    # This file
├── research.md                # Phase 0 decisions and alternatives
├── data-model.md              # Phase 1 entities and module ownership
├── quickstart.md              # Phase 1 parity validation guide
└── tasks.md                   # Phase 2 output only; not created in PLAN stage
```

`contracts/` is intentionally omitted. This refactor introduces no external
HTTP, WebSocket, Home Assistant service, entity-service, event, or public API
contract. Internal request, decision, and result interfaces are specified in
this plan and in [data-model.md](data-model.md).

### Source Code (repository root)

```text
custom_components/rental_control/
├── event_overrides.py                     # Public EventOverrides shell;
│                                          # current import path and tested
│                                          # compatibility seams remain here
├── event_overrides_helpers/
│   ├── __init__.py                        # Internal package marker/exports
│   ├── models.py                          # OverrideSnapshot, TrimConfig,
│   │                                      # MatchRequest, MatchResult,
│   │                                      # SlotUpdateRequest,
│   │                                      # SlotReservationRequest,
│   │                                      # EvictionDecision,
│   │                                      # action result decisions
│   ├── trim.py                            # _strip_prefix,
│   │                                      # trim_name-backed comparison,
│   │                                      # restore-full-name decisions
│   ├── matcher.py                         # Shared three-phase matcher used
│   │                                      # by both public mirror wrappers
│   ├── slot_bookkeeping.py                # occupied/free slot ordering,
│   │                                      # next-slot selection, UID owner
│   │                                      # and same-start helpers
│   ├── greedy_cleanup.py                  # async_check_overrides decision
│   │                                      # helpers and miss-count actions
│   ├── apply_dispatch.py                  # async_apply_plan action dispatch
│   │                                      # classification and skip reasons
│   ├── apply_clear.py                     # clear preflight and clear-result
│   │                                      # state mutation decisions
│   ├── apply_set.py                       # set/assign operation tokens,
│   │                                      # suppression payloads, rollback
│   ├── apply_update.py                    # update-times and overwrite/
│   │                                      # update-in-place decisions
│   └── diagnostics.py                     # diagnostics snapshot projection
├── coordinator.py                         # Existing async_update caller stays
├── coordinator_helpers/
│   ├── coordinator_config_shell.py        # Existing EventOverrides import stays
│   ├── coordinator_refresh_shell.py       # Existing async_apply_plan caller stays
│   └── coordinator_setup_shell.py         # Existing EventOverrides import stays
└── util.py                                # Existing ownership and async_update
                                           # calls stay source-compatible

tests/
├── unit/
│   ├── test_event_overrides.py            # Existing behavior oracle passes
│   │                                      # unchanged; add helper parity tests
│   ├── test_event_overrides_matcher.py    # New per-phase matcher parity tests
│   ├── test_event_overrides_apply.py      # New plan-application parity tests
│   ├── test_coordinator.py                # Existing caller compatibility
│   └── test_sensors.py                    # Existing no-greedy-call oracle
└── integration/
    ├── test_slot_concurrency.py           # Existing lock/dedup oracle
    └── test_refresh_cycle.py              # Existing end-to-end parity oracle
```

**Structure Decision**: Keep `event_overrides.py` as a module, not a package.
It is the verified public import path for coordinator setup/config helpers and
is also imported directly by tests for `EventOverrides`, `EventOverride`, and
`ReserveResult`. A file-to-package conversion could preserve the dotted import
with `event_overrides/__init__.py`, but it would add unnecessary risk to module
patching, direct test imports, and existing private regression seams. A sibling
`event_overrides_helpers/` package gives the implementation the same extraction
benefit while leaving the stable `custom_components.rental_control.event_overrides`
module and internal `from ..event_overrides import EventOverrides` imports unchanged.
No production caller imports from `event_overrides_helpers/`.

## Concrete Decomposition Design

### Public compatibility boundary

`custom_components/rental_control/event_overrides.py` remains the only public
event-override module. These verified production import sites must not change:

- `custom_components/rental_control/coordinator_helpers/coordinator_setup_shell.py`
- `custom_components/rental_control/coordinator_helpers/coordinator_config_shell.py`

These verified production callers must continue to use the same member names and
call styles:

- `custom_components/rental_control/coordinator.py:221-228` calls
  `event_overrides.async_update(slot, slot_code, slot_name, start, end,
  event_prefix)`.
- `custom_components/rental_control/coordinator_helpers/coordinator_refresh_shell.py:238-240`
  calls `event_overrides.async_apply_plan(self, plan, res_by_key)`.
- `custom_components/rental_control/util.py:337`, `:538`, and `:699` call
  `verify_slot_ownership(...)` before clear, set, and update-times service
  helpers.
- `custom_components/rental_control/util.py:996-998` calls
  `event_overrides.async_update(slot_num, "", "", start_of_day, start_of_day)`
  when Keymaster reset feedback is observed.

Every FR-017 member remains on `EventOverrides` with behavior-compatible
semantics: `overrides`, `ready`, `next_slot`, `trim_names`, `max_name_length`,
`event_prefix`, `prefix_length`, `persisted_mappings`, `pending_clear_slots`,
`pending_fences`, `reconciliation_active`, `diagnostics_snapshot`,
`suppress_state_changes`, `should_suppress_state_change`, `get_last_slot_error`,
`update_diagnostics_snapshot`, `load_persisted_mappings`,
`update_actual_state`, `get_actual_state`, `release_pending_clear_slot`,
`async_reserve_or_get_slot`, `async_update`, `verify_slot_ownership`,
`record_retry_failure`, `record_retry_success`, `async_apply_plan`,
`async_check_overrides`, `get_slot_name`, `get_slot_with_name`,
`get_slot_key_by_name`, date/time getters, and `update`.

FR-018 private seams also stay reachable from the class during implementation:
`_find_overlapping_slot`, `_slot_has_matching_event`, `_apply_clear`,
`_apply_overwrite_manual_change`, `_record_slot_error`, `_overrides`,
`_slot_uids`, `_slot_miss_counts`, `_pending_fences`, `_pending_clear_slots`,
and the consumed escalation state. Private methods may become short wrappers
around helpers, but tests that pin behavior must not need assertion rewrites.

### EventOverrides shell responsibilities

The shell keeps all live mutable state and Home Assistant/Keymaster boundaries:

1. construction of locks, slot maps, UID maps, miss counters, retry/error state,
   pending fences, actual-state cache, trim/prefix config, and diagnostics;
2. public properties and compatibility methods listed above;
3. lock acquisition and release around state-changing methods;
4. HA state reads for clear preflight and confirmed-empty set safety;
5. calls to `async_fire_clear_code`, `async_fire_set_code`, and
   `async_fire_update_times` in the current order;
6. mutation of `_overrides`, `_slot_uids`, `_slot_miss_counts`,
   `_pending_fences`, `_pending_clear_slots`, `_last_slot_errors`,
   `_suppressed_state_changes`, `_reconciliation_active`, and diagnostics;
7. compatibility wrappers for `_find_overlapping_slot`,
   `_slot_has_matching_event`, `_apply_clear`, `_apply_set`,
   `_apply_update_times`, and `_apply_overwrite_manual_change`.

Extracted helpers should receive immutable snapshots or explicit request objects
and return decisions. They must not call Home Assistant APIs, Keymaster service
helpers, `async_request_refresh()`, Store methods, or mutate `EventOverrides`
state directly. When a helper decides that a full name should be restored after
a trim/prefix match, it returns that restoration in `MatchResult`; the shell
applies the existing in-place `override["slot_name"] = full_name` mutation at the
same point as today.

### Shared three-phase matcher

`event_overrides_helpers.matcher` is the highest-risk extraction and must be
implemented first behind parity tests. Both `_find_overlapping_slot` and
`_slot_has_matching_event` build the same `MatchCatalog` from current overrides,
UIDs, sorted occupied slots, and `TrimConfig`, then delegate to the same
phase-level functions:

1. `find_uid_positive_exact_name()` implements phase 1. It normalizes incoming
   and stored UIDs, requires non-empty equal UIDs plus exact slot name, bypasses
   time overlap, and wins before every other candidate.
2. `find_exact_name_strict_overlap()` implements phase 2. It requires exact
   names and the strict interval rule `start_a < end_b AND start_b < end_a`.
   It preserves UID-owner exclusion, `exclude_slot`, preferred-slot tie-breaking,
   and the same-start bypass from PR #566: different non-empty UIDs are rejected
   unless UTC starts match, and an exact UID owner elsewhere still wins.
3. `find_trim_aware_fallback()` implements phase 3. It uses the current
   `trim_name` behavior through `TrimConfig(trim_names, max_name_length,
   event_prefix, prefix_length)`, including prefix stripping and
   `guest_max = max_name_length - prefix_length`. Phase 3a performs
   UID-positive trim matching with no overlap requirement; phase 3b performs
   trim matching with strict overlap and the same UID-owner/same-start rules as
   phase 2.

The mirror wrappers use these same phase functions in opposite orientations:

- `_find_overlapping_slot(slot_name, start, end, uid, exclude_slot)` asks the
  matcher for the winning slot for one incoming `EventIdentity`.
- `_slot_has_matching_event(slot, events)` asks the matcher to evaluate each
  current event against the same catalog and returns true only when the winning
  slot for that event is the checked slot.

This preserves the intentional difference between the methods: one returns the
selected slot for a candidate reservation, while the other checks whether a
specific stored slot still owns any current event. The phase semantics,
preferred-slot calculation, UID-owner exclusion, trim restoration, and strict
interval predicate live in one implementation, preventing future divergence.

### Plan application split

`async_apply_plan` remains the public reconciliation application entry point and
keeps the `reconciliation_active` lifecycle: set true before dispatch, finalize
diagnostics in `finally`, then set false under the lock. It delegates action
classification and small decisions to helpers while the shell preserves current
side effects.

`apply_dispatch.py` owns pure dispatch decisions: skip `NOOP` and `BLOCKED`,
map clear warning reasons, require reservations for set/update/overwrite
families, preserve returned result ordering, and ignore unknown action kinds.
The shell still iterates `plan.actions` in order and appends exactly one
`OperationResult` for each action currently appending one.

`apply_clear.py` owns clear-specific decisions but not service calls:

- operation fence and pending-clear token names remain generated by the shell;
- preflight decisions preserve fresh Keymaster read failures, unreadable states,
  confirmed-empty release, expected-name ownership checks, changed PIN presence,
  and conservative unconfirmed results;
- clear-result decisions preserve confirmed-empty release, failed-result error
  recording, lingering-name/PIN error recording, stale-token unconfirmed results,
  and the rule that reconciliation clears do not call `__assign_next_slot()`.

`apply_set.py` owns set/assign decisions: confirmed-empty safety, deterministic
set operation IDs based on plan id, slot, and identity hash, tentative override
assignment payloads, service-feedback suppression payloads, confirmed cleanup,
failed rollback, stale-token handling, error recording, and the rule that
reconciliation sets do not update the retired greedy `next_slot` selector.

`apply_update.py` owns update-times and overwrite/update-in-place decisions:
update-times suppression payloads, confirmed cached start/end updates, drift-log
field extraction without raw PINs, clear-before-replace ordering, failure or
unconfirmed clear short-circuiting, and replacement set plan-id generation.
The shell performs the actual `async_fire_update_times`, `_apply_clear`, and
`_apply_set` calls in the same order as today.

### Greedy cleanup and eviction tolerance

`greedy_cleanup.py` extracts pure cleanup decisions from `async_check_overrides`,
which remains a retained compatibility shim and is not reintroduced into
production stale-slot cleanup. Helpers receive the current calendar identities,
slot snapshots, current local date, max-events boundary, and current miss counts.
They return `EvictionDecision` values instructing the shell to reset a miss
count, increment a miss count, clear immediately, clear after the
`SLOT_MISS_THRESHOLD`, or preserve the slot.

The shell still holds `_lock` during the same override scan, calls
`async_fire_clear_code` with `expected_name=self.get_slot_name(slot)`, handles
exceptions conservatively, normalizes non-`OperationResult` returns to an
unconfirmed clear, preserves occupied state on unconfirmed/failed/lingering
clears, and calls `__assign_next_slot()` only in the retired greedy clear path.

### Trim/prefix and slot bookkeeping helpers

`trim.py` owns `_strip_prefix`, `_is_trimmed_match`, and construction of
`TrimConfig`. It must use `trim_name` exactly as the current source does rather
than replacing it with a substring or case-insensitive shortcut. Prefix handling
remains deterministic `prefix + " "` removal.

`slot_bookkeeping.py` owns pure ordering and lookup helpers: sorted occupied
slots, sorted empty slots above or below the last occupied slot, next-slot
selection for the retired greedy path, UID-owner checks, and same-start bypass
preferred-slot selection. The shell applies returned next-slot values and still
keeps the private `__assign_next_slot()` wrapper for current call points.

### Diagnostics extraction

`diagnostics.py` extracts the pure `update_diagnostics_snapshot` projection. It
must preserve keys, values, sorting, enum string values, retry-count ranges,
manual drift field parsing, pending-clear slots, last-slot errors, and raw PIN
redaction. The shell stores the returned dict in `_diagnostics_snapshot` at the
same point in `async_apply_plan`'s `finally` block.

### Parameter-count strategy

Current measured ground truth shows three 7-parameter methods when counting
`self`: `async_reserve_or_get_slot`, `async_update`, and `update`. The
implementation must satisfy the active parameter-count rule without breaking
real callers.

#### `async_reserve_or_get_slot`

Verified production status: the method is a retired greedy shim and is not
called from production code. Existing tests call it in these forms:

- all keyword fields: `slot_name=`, `slot_code=`, `start_time=`, `end_time=`,
  optionally `uid=`;
- four positional values `(slot_name, slot_code, start_time, end_time)` plus
  optional keyword `uid=`.

Implementation introduces `SlotReservationRequest` and a normalizer. The public
method becomes a thin compatibility wrapper with no more than six declared
parameters, for example:

```python
async def async_reserve_or_get_slot(
    self,
    request: SlotReservationRequest | str | None = None,
    *values: Any,
    **legacy: Any,
) -> ReserveResult: ...
```

The normalizer accepts a request object, current keyword fields, or current
positional fields plus `uid`/`prefix` keywords. It rejects unknown keywords in
new tests. The lock, prefix stripping, `_find_overlapping_slot` delegation,
miss-count reset, UID recording, next-slot assignment, and `ReserveResult`
semantics stay in the shell.

#### `async_update`

Verified production callers are `coordinator.py:221-228`, which passes slot,
code, name, start, end, and prefix positionally, and `util.py:996-998`, which
passes the five reset values positionally. Tests use five positional values and
keyword fields for `slot`, `slot_code`, `slot_name`, `start_time`, and
`end_time`.

Implementation introduces `SlotUpdateRequest` and a normalizer. The public
method becomes a thin compatibility wrapper with no more than six declared
parameters, for example:

```python
async def async_update(
    self,
    update: SlotUpdateRequest | int | None = None,
    *values: Any,
    **legacy: Any,
) -> None: ...
```

The normalizer accepts a request object, the current coordinator positional form
including optional prefix, the current util reset form, and current keyword
fields. The shell still serializes under `_lock`, strips prefixes, redirects
duplicate writes through `_find_overlapping_slot(exclude_slot=slot)`, mutates
`_overrides` and `_slot_uids`, resets miss counts, calls `__assign_next_slot()`,
and sets readiness exactly as today.

#### `update`

Verified production status: no current production call to
`event_overrides.update(...)` remains in `custom_components/`; the method is a
synchronous bootstrap/test compatibility helper. Existing tests call five
positional values and a few cases with `prefix=` as a keyword.

Implementation reuses `SlotUpdateRequest` and the same normalizer through a
synchronous wrapper with no more than six declared parameters, for example:

```python
def update(
    self,
    update: SlotUpdateRequest | int | None = None,
    *values: Any,
    **legacy: Any,
) -> None: ...
```

The wrapper preserves current copy-on-write assignment, prefix stripping, empty
slot clearing, miss-count reset, next-slot reassignment, readiness update, and
debug logging. Tests should pin legacy positional, legacy `prefix=`, and direct
request-object forms before removing the 7-parameter signature.

### Behavior-equivalence strategy

Current `origin/main` source plus existing tests are the oracle. Implementation
should first add focused parity tests around the existing shell, then extract one
concern at a time behind unchanged class methods. For identical override state,
UID maps, trim config, calendars, desired plans, actual state, and coordinator
fixtures, before/after results must match for:

- UID-positive exact-name matches, including date shifts with no overlap;
- exact-name plus strict overlap matches and non-overlap misses;
- same-start UID bypass acceptance, rejection on different starts, exact UID
  owner precedence, preferred-slot tie-breaking, and `exclude_slot` behavior;
- trim-aware and prefix-aware UID-positive and overlap fallback, including
  restored full-name mutation only when the current engine mutates it;
- duplicate-name cases, missing UIDs, empty UIDs, duplicate UID owners, and
  no-match cases;
- `_find_overlapping_slot` and `_slot_has_matching_event` mirror decisions for
  every phase above;
- eviction miss-count increment/reset/threshold behavior and immediate clear
  behavior for stale, past, malformed, empty-calendar, and beyond-boundary slots;
- plan actions for `clear`, `retry_clear`, `reset`, `set`, `assign`,
  `update_times`, `overwrite_manual_change`, `update_in_place`, `noop`, and
  `blocked`;
- clear preflight, confirmed-empty set checks, pending fences, pending-clear
  bookkeeping, service-feedback suppression, retry/error state, diagnostics, and
  `reconciliation_active` transitions.

No helper may introduce extra Home Assistant state writes, additional
coordinator refreshes, blocking I/O, additional Store authority, new matching
semantics, new reconciliation actions, new services, new sensors, or changed
caller behavior.

### `aislop` directive removal

The implementation must remove the current file-level directive:

```python
# aislop-ignore-file complexity/file-too-large complexity/function-too-long -- Existing module size is outside this emergency fix scope.
```

There is no hallucinated-import directive on `event_overrides.py` to preserve or
remove. The directive must be removed only after measuring the resulting
`event_overrides.py` and `event_overrides_helpers/` files and confirming every
file is below 400 lines, every project-owned function is 80 lines or fewer, and
every project-owned parameter list is no more than six parameters. No
replacement complexity suppression should be added for this feature.

## Phase 0 Research

Research is complete in [research.md](research.md). It records the sibling
helper-package decision, shared matcher design, shell/applier boundary,
parameter-bundle strategy, eviction and diagnostics extraction, and
behavior-parity approach, with alternatives grounded in the current source.

## Phase 1 Design Artifacts

- [research.md](research.md): required and complete.
- [data-model.md](data-model.md): required and complete.
- [quickstart.md](quickstart.md): required and complete.
- `contracts/`: omitted because no external API, service, event, entity, or
  public `EventOverrides` contract is introduced or changed.
- `update-agent-context.sh`: intentionally not run. The plan adds no new
  language, framework, database, runtime, package manager, or agent-relevant
  technology beyond the Python/Home Assistant stack already documented in the
  repository.

## Post-Design Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I: Code Quality & Testing | PASS | Quickstart requires existing event override, slot concurrency, refresh-cycle, coordinator, sensor, and utility tests unchanged plus focused matcher, cleanup, application, diagnostics, and wrapper compatibility parity tests. |
| II: Atomic Commit Discipline | PASS | PLAN artifacts are one docs-only change; future implementation can be split into small extraction and test commits. |
| III: Licensing & Attribution | PASS | `plan.md`, `research.md`, `data-model.md`, and `quickstart.md` include SPDX headers. |
| IV: Pre-Commit Integrity | PASS | The PR must pass hooks and CI without bypass flags. |
| V: Agent Co-Authorship & DCO | PASS | The planned commit uses sign-off and the requested co-author trailer. |
| VI: User Experience Consistency | PASS | All public imports, FR-017 members, FR-018 regression seams, caller call styles, matching results, operation results, and diagnostics are preserved. |
| VII: Performance Requirements | PASS | Extracted helpers are in-memory decisions; HA reads, service calls, Store-neutral cache updates, and refresh behavior stay unchanged on the shell. |

**Gate result: PASS** — no plan-stage constitution violations. Existing
`event_overrides.py` complexity debt remains the implementation target.

## Complexity Tracking

> No plan-stage constitution violations require justification. The existing
> event-overrides complexity debt remains the implementation target, and the
> implementation must measure file lengths, function lengths, and parameter
> counts immediately before removing the `event_overrides.py` complexity
> `aislop` directive.

## Phase Notes

- PLAN stage stops here. Do not create `tasks.md` or modify runtime source in
  this PR.
- Implementation must treat current `origin/main` as truth. Planning shorthand
  and issue text are secondary when they disagree with `event_overrides.py`.
- Keep the refactor behavior-preserving. Any discovered behavior bug or new
  lock-code business rule belongs in a separate issue/feature, not this
  decomposition.
