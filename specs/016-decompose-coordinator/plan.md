<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Implementation Plan: Decompose Coordinator

**Feature**: `016-decompose-coordinator` | **Planning Branch**:
`016-decompose-coordinator-plan` | **Date**: 2026-06-27 | **Spec**:
[spec.md](spec.md)
**Input**: Feature specification from
`specs/016-decompose-coordinator/spec.md` and GitHub issue #574

## Summary

Decompose `custom_components/rental_control/coordinator.py` without changing
Home Assistant-visible behavior. The current 2,948-line source is the
load-bearing contract: it defines the `RentalControlCoordinator`
`DataUpdateCoordinator`, calendar fetch and iCal parsing, reservation and ghost
reservation construction, physical Keymaster slot observation, first-load
Keymaster override bootstrap, Store adoption/sync, check-in protection,
configuration updates, event override updates, and refresh-cycle orchestration.

The implementation will keep `coordinator.py` as the public Home Assistant
orchestration shell that owns `RentalControlCoordinator`. Focused helper modules
will live in a sibling internal package,
`custom_components/rental_control/coordinator_helpers/`. Public callers that do
`from .coordinator import RentalControlCoordinator` will keep importing the
class from the same module, and every FR-012 member remains on the class with
unchanged behavior. Extracted helpers must be behavior-preserving only: no new
calendar rules, lock-code rules, reconciliation behavior, Store authority,
refresh scheduling, Home Assistant state writes, diagnostics fields, or public
caller behavior.

## Technical Context

**Language/Version**: Python >=3.14.2
**Primary Dependencies**: Home Assistant runtime >=2026.4.0 per `hacs.json`;
dev/test dependency `homeassistant>=2026.6.0` per `pyproject.toml`;
`pytest-homeassistant-custom-component`, `icalendar>=7.0.0`, and
`x-wr-timezone>=2.0.0`
**Storage**: Home Assistant `Store` data under the coordinator remains
cache-only slot metadata; no new persistent storage and no Store authority over
current physical Keymaster state or current calendar data
**Testing**: `uv run pytest tests/`; targeted coordinator and refresh coverage
in `tests/unit/test_coordinator.py`,
`tests/unit/test_coordinator_buffer_update.py`,
`tests/unit/test_event_overrides.py`,
`tests/unit/test_keymaster_event_diagnostics.py`,
`tests/unit/test_slot_reconciliation.py`, `tests/unit/test_calendar.py`,
`tests/unit/test_sensors.py`, `tests/integration/test_refresh_cycle.py`,
`tests/integration/test_checkin_tracking.py`, and
`tests/integration/test_slot_concurrency.py`; ruff via
`uv run ruff check custom_components/ tests/`; pre-commit hooks for reuse,
ruff, mypy, interrogate, yamllint, actionlint, and gitlint
**Target Platform**: Home Assistant custom integration on the HA asyncio event
loop for Linux, HA OS, Docker, and HACS-managed installs
**Project Type**: Single Home Assistant custom integration
**Performance Goals**: Refresh behavior remains bounded by one calendar fetch,
one iCal parse executor pass, one current Keymaster state observation over the
managed slot range, one desired-plan computation, and the same Store save and
service-call order as today. Hot-path helpers perform only in-memory work over
already-read data and do not add I/O, refreshes, HA state writes, Store reads, or
user-visible delay.
**Constraints**: Documentation-only PLAN PR; no production code. Runtime
implementation must preserve `RentalControlCoordinator` imports, FR-012 class
members, calendar outputs, event selection, observed slots, reservations, ghost
reservations, check-in protection flags, desired plans, diagnostics, Store
metadata, Keymaster service calls, logging decisions tested by the suite, and
refresh scheduling side effects.
**Scale/Scope**: One 2,948-line coordinator module becomes a small public shell
plus an internal helper package. The implementation target is all
coordinator-related files below 400 lines, project-owned functions below 80
lines, and project-owned parameter lists no more than six parameters.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I: Code Quality & Testing | PASS | Plan requires existing coordinator, reconciliation, event override, calendar, sensor, check-in, and refresh-cycle tests to pass unchanged and adds focused parity tests for extracted concerns. |
| II: Atomic Commit Discipline | PASS | This PR is one docs-only PLAN commit. Future implementation can split extraction, shell wiring, parameter bundling, and tests into atomic commits. |
| III: Licensing & Attribution | PASS | New markdown artifacts include SPDX headers. Future Python helper modules must include project SPDX headers. |
| IV: Pre-Commit Integrity | PASS | No hook bypass is planned. Quickstart defines local validation before implementation merge. |
| V: Agent Co-Authorship & DCO | PASS | The PLAN commit uses `git commit -s` and the requested AI co-author trailer. |
| VI: User Experience Consistency | PASS | Public coordinator imports, entity-facing attributes, service helper behavior, diagnostics, and refresh outcomes are explicitly preserved. |
| VII: Performance Requirements | PASS | The split keeps HA side effects in the coordinator shell and extracted helpers in-memory over existing inputs. |

**Gate result: PASS** — no violations. Proceeding to Phase 0.

## Project Structure

### Documentation (this feature)

```text
specs/016-decompose-coordinator/
├── plan.md                    # This file
├── research.md                # Phase 0 decisions and alternatives
├── data-model.md              # Phase 1 data structures and module ownership
├── quickstart.md              # Phase 1 parity validation guide
└── tasks.md                   # Phase 2 output only; not created in PLAN stage
```

`contracts/` is intentionally omitted. This refactor introduces no external
HTTP, WebSocket, Home Assistant service, entity-service, event, or public API
contract. Internal request and decision interfaces are specified in this plan
and in [data-model.md](data-model.md).

### Source Code (repository root)

```text
custom_components/rental_control/
├── coordinator.py                       # Public DataUpdateCoordinator shell;
│                                        # RentalControlCoordinator remains here
├── coordinator_helpers/
│   ├── __init__.py                      # Internal package marker/typed exports
│   ├── models.py                        # CalendarParseContext,
│   │                                    # ReservationBuildContext,
│   │                                    # ObservedSlotQuery,
│   │                                    # EventOverrideUpdate,
│   │                                    # KeymasterSlotSnapshot,
│   │                                    # adoption/store/check-in decisions
│   ├── calendar_parsing.py              # iCal event filtering, time selection,
│   │                                    # override-aware event conversion,
│   │                                    # CalendarEvent construction helpers
│   ├── reservations.py                  # Reservation and ghost-reservation
│   │                                    # builders, display names, aliases,
│   │                                    # manual PIN preservation decisions
│   ├── slot_matching.py                 # ObservedSlotQuery matching,
│   │                                    # duplicate-name/date-window pairing,
│   │                                    # physical-name matching helpers
│   ├── keymaster_observation.py         # Keymaster state snapshot classification
│   │                                    # into ManagedSlot and actual-state data
│   ├── keymaster_bootstrap.py           # First-load override setup and Store
│   │                                    # adoption decisions for physical slots
│   ├── checkin_protection.py            # Checked-in/checked-out reservation
│   │                                    # protection decisions and synthesis
│   ├── store_sync.py                    # Cache-only Store mapping updates from
│   │                                    # plans, results, aliases, diagnostics
│   ├── config_update.py                 # Config snapshot parsing, stale
│   │                                    # override decisions, buffer updates
│   └── diagnostics.py                   # Latest-diagnostics projection and
│                                        # keymaster event-recording utilities
├── event_overrides.py                   # Existing override manager and apply
│                                        # plan integration remain source-compatible
├── util.py                              # Existing state-change listener call to
│                                        # coordinator.update_event_overrides stays
└── reconciliation/                      # Existing package surface imported by
                                         # coordinator remains unchanged

tests/
├── unit/
│   ├── test_coordinator.py                  # Existing public/shell oracle;
│   │                                       # add wrapper compatibility tests
│   ├── test_coordinator_buffer_update.py    # Existing buffer update oracle
│   ├── test_coordinator_parsing.py          # New focused iCal helper parity
│   ├── test_coordinator_reservations.py     # New focused reservation parity
│   ├── test_coordinator_observation.py      # New focused slot observation parity
│   ├── test_coordinator_bootstrap.py        # New setup/adoption parity
│   ├── test_coordinator_store_sync.py       # New cache-only Store parity
│   ├── test_event_overrides.py              # Existing apply/update behavior
│   ├── test_slot_reconciliation.py          # Existing desired-plan oracle
│   └── test_keymaster_event_diagnostics.py  # Existing diagnostics oracle
└── integration/
    ├── test_refresh_cycle.py                # End-to-end refresh parity oracle
    ├── test_checkin_tracking.py             # Check-in protection/restore oracle
    └── test_slot_concurrency.py             # Apply/callback ordering oracle
```

**Structure Decision**: Keep `coordinator.py` as a module, not a package, because
it is the public import path and must remain the Home Assistant entity and
orchestration boundary. A `coordinator/` subpackage cannot coexist with
`coordinator.py`; converting the public module into a package would add avoidable
import risk for no behavior benefit. The implementation therefore adds the
sibling internal package `coordinator_helpers/` and imports helpers from the
existing `coordinator.py` shell. No production caller imports from
`coordinator_helpers/`.

## Concrete Decomposition Design

### Public compatibility boundary

`custom_components/rental_control/coordinator.py` remains the only public
coordinator module. The implementation must not require changes to these
verified production import sites:

- `custom_components/rental_control/__init__.py`
- `custom_components/rental_control/calendar.py`
- `custom_components/rental_control/switch.py`
- `custom_components/rental_control/sensors/calsensor.py` under `TYPE_CHECKING`
- `custom_components/rental_control/sensors/checkinsensor.py` under
  `TYPE_CHECKING`

Every FR-012 member remains on `RentalControlCoordinator` with behavior-compatible
semantics: `monitored_locknames`, `device_info`, `entry_id`, `unique_id`,
`version`, `latest_plan`, `latest_overflow`,
`latest_reconciliation_diagnostics`, `get_slot_assignment`, `get_slot_code`,
`get_overflow_reason`, `async_get_events`,
`async_setup_keymaster_overrides`, `async_load_slot_store`,
`get_persisted_slot_mappings`, `async_save_slot_store`,
`async_adopt_keymaster_slots`, `update_config`, `update_event_overrides`,
`created`, `lockname`, `start_slot`, `max_events`, `event_overrides`,
`event_prefix`, `code_generator`, `code_length`, `code_buffer_before`,
`code_buffer_after`, `trim_names`, `max_name_length`, `event`, and
`keymaster_event_diagnostics`.

Internal helper modules may define new dataclasses and functions, but callers
outside `coordinator.py` must not depend on them. Existing tests that construct,
mock, or inspect the coordinator continue to import `RentalControlCoordinator`
from `custom_components.rental_control.coordinator`.

### Coordinator shell responsibilities

`coordinator.py` keeps Home Assistant lifecycle, state boundaries, and ordering:

1. `RentalControlCoordinator.__init__` parses config, creates `EventOverrides`,
   registers device metadata, discovers child locks, and calls `super().__init__`.
   Repeated config-field assignment can delegate to a config snapshot helper, but
   construction side effects and public attributes stay on the class.
2. `_async_fetch_calendar` owns the HTTP request, timeout handling, response
   release, executor calls for `Calendar.from_ical` and `x_wr_timezone`, and
   `UpdateFailed` wrapping. It delegates parsed iCal conversion only after the
   text has been fetched and converted.
3. `_async_update_data` remains the refresh-cycle orchestrator. It preserves
   cached-data fallback, miss tolerance, `self.event` selection,
   observation -> reservation -> protection -> `compute_desired_plan` ->
   `async_apply_plan` -> Store sync -> latest-plan assignment -> Store save ->
   child-lock rediscovery ordering.
4. HA state reads, Store writes, service calls, notification creation, listener
   refreshes, and `async_request_refresh()` remain in the shell or existing
   service/helper modules. Extracted helpers return decisions or data objects;
   the shell applies side effects in the current order.
5. Public and test-consumed methods remain as thin wrappers when implementation
   moves their bodies to helper modules.

### iCal fetch and parsing

`calendar_parsing.py` owns the behavior currently in `_ical_parser` and
`_ical_event` after the calendar object is available:

- RRULE logging and skip behavior;
- Smoobu Check-in/Check-out extra-event filtering;
- age and far-future filtering;
- `ignore_non_reserved` handling for Blocked and Not available summaries;
- slot-name extraction through `get_slot_name()`;
- `EventOverrides.get_slot_with_name()` lookup;
- Honor Event Times priority order: explicit PMS times, description times,
  genuine manual override fallback, configured defaults, and disabled override
  fallback;
- buffer-aware physical override time comparison;
- timezone conversion to UTC and back to coordinator timezone;
- event-prefix summary mutation;
- UID normalization and final sort by start time.

The shell passes a `CalendarParseContext` containing timezone, configured
check-in/check-out times, event prefix, `ignore_non_reserved`,
`honor_event_times`, buffer values, and an optional override lookup callback.
Helpers do not fetch network data, call HA APIs, or schedule refreshes.

### Reservation and ghost-reservation builders

`reservations.py` owns `_build_reservations` and `_build_ghost_reservations`
logic through pure builders that accept `ReservationBuildContext`, current
calendar events, current managed-slot observations, current Store mappings, and
active check-in windows supplied by the shell.

The implementation preserves:

- ordering by coerced event start, end, and summary;
- `get_slot_name()` filtering;
- buffered date-window grouping and duplicate-name counts;
- display names through `_format_display_slot_name()` semantics;
- identity keys via `make_reservation_fingerprint()`;
- UID aliases and booking aliases from the reconciliation package;
- generated code and observed manual PIN preservation rules;
- `code_source` values and sensor lookup keys;
- invalid-reservation warning/skip behavior;
- ghost missing-count increments, pending-set to pending-clear transition,
  physical-name mismatch fencing, date parsing, fingerprint history, and the rule
  that raw PINs are never reconstructed from Store.

The coordinator wrapper supplies code-generation callbacks and active check-in
windows so reservation helpers remain side-effect-free. Store mappings may be
mutated only by the coordinator wrapper or a returned `GhostReservationResult`
that the wrapper applies to the live cache in the same order as today.

### Physical slot observation and matching

`keymaster_observation.py` introduces `KeymasterSlotSnapshot` for the HA states
currently read in `_observe_managed_slots`: name, PIN, use-date-range switch,
enabled switch, start datetime, and end datetime. The coordinator shell reads
those states from `self.hass.states` and calls pure classification helpers that
return both a `_ManagedSlot` and the actual-state diagnostics dict. The shell then
calls `EventOverrides.update_actual_state()` exactly once per slot as it does
today.

`slot_matching.py` owns duplicate-name/date-window matching currently in
`_find_observed_slot_by_name`, `_select_ordered_physical_subset`,
`_select_partial_ordered_pairings`, `_physical_slot_name_matches_name`, and
`_physical_slot_name_matches_reservation`. It must preserve prefix stripping,
normalized name forms, consumed-slot tracking, exact date matches, required date
matches, ordered physical subset selection, partial pairings, shifted-date
fallback, unknown-date blocking, and duplicate-name expected-count behavior.

### `_find_observed_slot_by_name` parameter reduction

Current ground truth is a 12-parameter coordinator method:

```python
def _find_observed_slot_by_name(
    self,
    managed_slots,
    slot_name,
    display_slot_name,
    consumed_slots=None,
    desired_start=None,
    desired_end=None,
    require_date_match=False,
    reserved_date_windows=None,
    ordered_date_windows=None,
    block_unknown_date_fallback=False,
    expected_name_count=1,
): ...
```

Implementation introduces `ObservedSlotQuery` in `coordinator_helpers.models`
with those matching criteria. New internal calls from reservation and check-in
protection code pass one query object to `slot_matching.find_observed_slot()`.
`RentalControlCoordinator._find_observed_slot_by_name` remains as a compatibility
wrapper with no more than six declared parameters, for example
`(self, query_or_slots, slot_name=None, display_slot_name=None, **criteria)`. The
wrapper accepts the current three-argument test call from
`tests/unit/test_coordinator.py`, accepts existing keyword criteria during the
transition, constructs `ObservedSlotQuery`, delegates, and returns the same
`ManagedSlot | None`. This satisfies the parameter-count gate without changing
matching behavior or breaking current in-repository callers.

### Keymaster override setup, adoption, and Store sync

`keymaster_bootstrap.py` owns pure decisions for the two heavy setup paths:

- `async_setup_keymaster_overrides`: classify readable/unreadable slots,
  partially reset slots that need forced clear, code-bearing unnamed slots that
  need an adopted placeholder, default date ranges when Keymaster date limits are
  off, and the override update payload to record.
- `async_adopt_keymaster_slots`: build cache-only mappings for populated
  physical slots when Store is empty, preserving adopted identity keys,
  `occupied` versus `pending_clear`, raw-PIN redaction, date-range parsing,
  placeholder names, existing-slot skips, and Store schema metadata.

The coordinator shell retains async service calls (`async_fire_clear_code`),
`EventOverrides.async_update`, Store mutation, `async_save_slot_store()`, and
`EventOverrides.load_persisted_mappings()` ordering.

`store_sync.py` owns the cache-only mapping update currently in
`_sync_slot_store_from_plan`. It computes mapping removals for confirmed clears,
skips failed sets, removes stale keys for the same physical slot, emits identity,
actual-state, alias, fingerprint, `last_plan`, and metadata updates, and returns a
mutation result. The shell applies that result to `self._slot_mappings` and then
reloads mappings into `EventOverrides` exactly as today.

### Check-in protection

`checkin_protection.py` owns decisions from `_apply_checkin_protection` and
`_active_checkin_windows_for_name`. The shell reads the check-in sensor object
from `hass.data`, converts its state and attributes into a snapshot, and passes
that snapshot to the helper with reservations and managed slots. The helper
returns one of:

- no change;
- mark the exact or unique matching reservation `protected_active=True`;
- mark the exact or unique matching reservation `checked_out=True`;
- synthesize the missing active physical stay reservation from matched Keymaster
  state.

The shell mutates the live `Reservation` objects or appends the synthesized
reservation in the same sequence as the current method. It preserves duplicate
name start/end matching, buffered physical-window comparison, same-name slot
safety, generated versus manual observed code source, and sensor lookup keys.

### Config updates and buffer updates

`config_update.py` extracts config parsing, stale `EventOverrides` detection,
child-lock reset decisions, and buffer-update payload construction. The public
`update_config()` method remains on `RentalControlCoordinator` and keeps the
current behavior: config fields update in place, `EventOverrides` is recreated
when lockname, range, or capacity changes, `async_setup_keymaster_overrides()` is
called before persisted mappings reload, parent/child lock discovery is refreshed
on lockname changes, buffer changes call `_async_update_buffer_times()`, and the
method ends with `async_request_refresh()`.

The buffer-time helper may compute old-buffer reversal and new-buffer application
in a pure function, but the coordinator shell continues to suppress feedback,
call Keymaster datetime services, update override cache entries, and log gather
results in the current order.

### `update_event_overrides` parameter reduction

Current ground truth is a consumed 7-parameter coordinator method:

```python
async def update_event_overrides(
    self,
    slot,
    slot_code,
    slot_name,
    start_time,
    end_time,
    *,
    request_refresh=True,
): ...
```

Verified call sites are:

- `custom_components/rental_control/util.py:1152-1158`, which calls
  `await coordinator.update_event_overrides(slot_num, slot_code_value,
  slot_name_value, start_time, end_time)` from the Keymaster state listener;
- `custom_components/rental_control/coordinator.py:549-556`, which calls the
  same entry point with `request_refresh=False` during bootstrap;
- `tests/unit/test_coordinator.py:1459-1465` and `:1486-1492`, which use the
  current keyword names.

Implementation introduces `EventOverrideUpdate` in
`coordinator_helpers.models`. New coordinator-internal calls may pass
`EventOverrideUpdate` directly. The public coordinator method remains callable by
current positional and keyword callers while declaring no more than six
parameters, for example
`async def update_event_overrides(self, update=None, *values, request_refresh=True,
**legacy)`. It normalizes one of three accepted forms into `EventOverrideUpdate`:
new dataclass input, current five positional values, or current keyword values
(`slot`, `slot_code`, `slot_name`, `start_time`, `end_time`). It then delegates to
`EventOverrides.async_update(..., self.event_prefix)` and optionally calls
`async_request_refresh()` exactly as today. Tests must pin all three forms so the
parameter gate is satisfied without breaking the `util.py` caller.

### Diagnostics and event recording

`diagnostics.py` centralizes coordinator-owned projection helpers while
preserving existing outputs:

- `latest_overflow` and `latest_reconciliation_diagnostics` continue to expose
  the same dictionaries and lists from the latest desired plan;
- `keymaster_event_diagnostics` remains the same `deque(maxlen=10)` on the
  coordinator and the listener still appends the same redacted event records;
- diagnostics-triggered check-in sensor `async_write_ha_state()` remains in the
  listener path with the same dispositions;
- `EventOverrides.diagnostics_snapshot` and actual-state snapshots remain
  redacted and structurally unchanged.

### Reconciliation package integration

The coordinator continues importing and using the existing reconciliation package
surface required by FR-018: `DesiredPlan`, `ManagedSlot`, `Reservation`,
`SlotStatus`, `compute_desired_plan`, `extract_booking_aliases`,
`make_reservation_fingerprint`, and `normalize_slot_name_for_fingerprint`.
Extraction must not duplicate or replace reconciliation algorithms. Reservation
helpers prepare equivalent inputs; `_async_update_data` still calls
`compute_desired_plan()` once with the same logical arguments and passes the
result to `EventOverrides.async_apply_plan()` in the same refresh cycle.

### Behavior-equivalence strategy

Current `origin/main` source plus existing tests are the oracle. The
implementation should first add serialization/parity helpers in tests, then
perform each extraction behind the unchanged coordinator methods. For identical
calendar text, config, Store mappings, check-in sensor state, and Keymaster HA
states, before/after results must match for:

- parsed `CalendarEvent` summaries, descriptions, locations, UIDs, starts, ends,
  order, and current-event selection;
- observed `ManagedSlot` values and actual-state diagnostics;
- regular and ghost `Reservation` fields, lookup keys, aliases, fingerprints,
  missing counts, generated/manual code source, protected-active flags, and
  checked-out flags;
- `DesiredPlan` selected/overflow/action/diagnostics contents and action order;
- `EventOverrides.async_apply_plan()` operation order and results;
- Store mappings, `latest_plan`, `latest_overflow`,
  `latest_reconciliation_diagnostics`, and keymaster event diagnostics;
- side-effect order for service calls, Store saves, refresh requests, and HA
  state writes.

No helper may introduce extra calendar fetches, extra coordinator refreshes,
blocking I/O on the HA event loop, additional Home Assistant state writes,
authoritative Store reads, or new user-visible delays.

### `aislop` directive removal

The implementation must keep the legitimate Home Assistant runtime import
suppression:

```python
# aislop-ignore-file ai-slop/hallucinated-import -- Provided by Home Assistant runtime.
```

It must remove only the separate temporary complexity directive:

```python
# aislop-ignore-file complexity/file-too-large complexity/function-too-long -- Existing module size is outside this emergency fix scope.
```

Before removing that directive, implementation must measure the resulting
coordinator feature area and confirm every file is below 400 lines, every
project-owned function is below 80 lines, and every project-owned parameter list
is no more than six parameters. No replacement complexity suppression should be
added for this feature.

## Phase 0 Research

Research is complete in [research.md](research.md). It records the sibling
internal package decision, public compatibility boundary, parameter-bundle
strategies for `_find_observed_slot_by_name` and `update_event_overrides`,
pure-extraction pattern for mutating coordinator functions, reconciliation
integration, and behavior-parity approach, with alternatives grounded in the
current source.

## Phase 1 Design Artifacts

- [research.md](research.md): required and complete.
- [data-model.md](data-model.md): required and complete.
- [quickstart.md](quickstart.md): required and complete.
- `contracts/`: omitted because no external API, service, event, entity, or
  public coordinator contract is introduced or changed.
- `update-agent-context.sh`: intentionally not run. The plan adds no new
  language, framework, database, runtime, package manager, or agent-relevant
  technology beyond the Python/Home Assistant stack already documented in the
  repository.

## Post-Design Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I: Code Quality & Testing | PASS | Quickstart requires existing coordinator and integration tests unchanged plus focused parity tests for iCal parsing, reservations, observation, bootstrap/adoption, Store sync, check-in protection, diagnostics, and compatibility wrappers. |
| II: Atomic Commit Discipline | PASS | PLAN artifacts are one docs-only change; future implementation can be split into small extraction and test commits. |
| III: Licensing & Attribution | PASS | `plan.md`, `research.md`, `data-model.md`, and `quickstart.md` include SPDX headers. |
| IV: Pre-Commit Integrity | PASS | The PR must pass hooks and CI without bypass flags. |
| V: Agent Co-Authorship & DCO | PASS | The planned commit uses sign-off and the requested co-author trailer. |
| VI: User Experience Consistency | PASS | All public coordinator imports, FR-012 members, caller call styles, diagnostics, and entity-facing behavior are preserved. |
| VII: Performance Requirements | PASS | Extracted helpers are in-memory and shell-applied; no new I/O, refreshes, Store authority, state writes, or Keymaster operations are introduced. |

**Gate result: PASS** — no plan-stage constitution violations. Existing
coordinator complexity debt remains the implementation target.

## Complexity Tracking

> No plan-stage constitution violations require justification. The existing
> coordinator complexity debt remains the implementation target, and the
> implementation must measure line counts, function lengths, and parameter counts
> immediately before removing the coordinator complexity `aislop` directive.

## Phase Notes

- PLAN stage stops here. Do not create `tasks.md` or modify production code in
  this PR.
- Implementation must treat current `origin/main` as truth. Planning shorthand
  and issue text are secondary when they disagree with `coordinator.py`.
- Keep the refactor behavior-preserving. Any discovered behavior bug or business
  rule improvement belongs in a separate issue/feature, not this decomposition.
