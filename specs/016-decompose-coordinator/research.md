<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Research: Decompose Coordinator

## Decision: Keep `coordinator.py` as the public shell

**Decision**: Keep `custom_components/rental_control/coordinator.py` as the
module that defines `RentalControlCoordinator`. Add a sibling internal package,
`custom_components/rental_control/coordinator_helpers/`, for extracted helpers.

**Rationale**: The verified production callers import
`RentalControlCoordinator` from `.coordinator` in `__init__.py`, `calendar.py`,
and `switch.py`, with type-checking imports in `sensors/calsensor.py` and
`sensors/checkinsensor.py`. The class is also the Home Assistant
`DataUpdateCoordinator` boundary that owns lifecycle, refresh scheduling,
`hass.data`, Store writes, and service-call ordering. Keeping the class in the
same module preserves the import path and avoids a file-to-package migration.
A `coordinator/` subpackage cannot coexist with `coordinator.py`, so a sibling
helper package gives decomposition without changing public imports.

**Alternatives considered**:

- Convert `coordinator.py` to a `coordinator/` package with `__init__.py`
  re-exporting `RentalControlCoordinator`. This mirrors the reconciliation
  package approach but adds avoidable import risk and requires moving the public
  Home Assistant shell.
- Add many flat sibling modules such as `coordinator_parsing.py` and
  `coordinator_store.py`. This avoids the file/package collision but scatters one
  feature across the integration root and makes it harder to keep internal
  helpers private.
- Leave all helpers in `coordinator.py`. This cannot satisfy the file-size and
  function-length goals or remove the complexity suppression.

## Decision: Extract helpers by concern, not by line ranges

**Decision**: Group helpers around behavior concerns: iCal parsing,
reservation/ghost building, physical slot matching, Keymaster observation,
Keymaster bootstrap/adoption, check-in protection, Store sync, config/buffer
updates, and diagnostics.

**Rationale**: The heavy methods are not independent line chunks. For example,
`_build_reservations` depends on slot matching, buffers, code generation, active
check-in windows, and reconciliation fingerprint helpers. `_async_update_data`
must stay as orchestration because it controls miss tolerance, current event
selection, `compute_desired_plan`, `EventOverrides.async_apply_plan`, Store sync,
and child-lock rediscovery order. Concern-based modules keep related decisions
close while preserving the shell's side-effect order.

**Alternatives considered**:

- Split each large method into one same-named module. This would reduce file size
  but would not isolate shared matching, buffer, and Store concepts.
- Move all Keymaster behavior into `event_overrides.py`. That module already owns
  apply-plan behavior and is itself large; adding observation and bootstrap would
  make another central file instead of decomposing the coordinator.

## Decision: Prefer pure decision helpers with shell-applied side effects

**Decision**: Extract pure or mostly pure helpers that accept snapshots/context
objects and return data, decisions, or mutation plans. Keep HA state reads,
network fetches, Store writes, service calls, refresh requests, and entity state
writes in `RentalControlCoordinator` or existing Home Assistant-facing modules.

**Rationale**: FR-019 forbids new hot-path I/O, refreshes, state writes, and
Store authority. The current source is safety-critical because refresh ordering
controls physical lock-code behavior. Pure helpers can be parity-tested with
snapshots, while the shell preserves the exact existing order of side effects.

**Alternatives considered**:

- Move full methods unchanged into helper modules that accept `self`. This is the
  fastest extraction but hides HA side effects inside internal modules and makes
  FR-019 harder to audit.
- Introduce service classes with references to `hass`, Store, and
  `EventOverrides`. This adds stateful collaborators and lifecycle complexity
  without a behavior benefit.

## Decision: Use `ObservedSlotQuery` for slot matching

**Decision**: Introduce `ObservedSlotQuery` to bundle the current
`_find_observed_slot_by_name` criteria. New internal code calls
`slot_matching.find_observed_slot(query, prefix)`. The coordinator method remains
a compatibility wrapper with no more than six declared parameters.

**Rationale**: Current ground truth has 12 declared parameters including `self`:
managed slots, slot names, consumed slots, desired window, date-match mode,
reserved windows, ordered windows, unknown-date fallback, and expected name
count. Those values form one matching query. Bundling them makes the helper
explicit and satisfies the parameter-count threshold while preserving duplicate
name, date-window, consumed-slot, prefix, and fallback behavior. The wrapper also
keeps the existing three-argument test call working.

**Alternatives considered**:

- Keep the current signature and suppress the parameter-count violation. FR-013
  forbids that.
- Split the method into multiple functions by branch. That may reduce function
  length but the entry point would still exceed the parameter threshold unless a
  query object is introduced.
- Change all callers to the dataclass and delete the method. Existing tests and
  any internal compatibility expectations would lose a current coordinator entry
  point unnecessarily.

## Decision: Use `EventOverrideUpdate` with a compatibility wrapper

**Decision**: Introduce `EventOverrideUpdate` for the slot, PIN, name, start, and
end values. New code may pass this dataclass to `update_event_overrides`, while
the coordinator method keeps accepting current positional and keyword callers
through `*values` and `**legacy` with no more than six declared parameters.

**Rationale**: `util.py` currently calls
`coordinator.update_event_overrides(slot_num, slot_code_value, slot_name_value,
start_time, end_time)`. Coordinator bootstrap calls the same method with
`request_refresh=False`, and existing unit tests use keyword names. A direct
signature change would break at least one of those call styles. A compatibility
wrapper can normalize all current forms into `EventOverrideUpdate`, call
`EventOverrides.async_update(..., self.event_prefix)`, and preserve the optional
refresh request exactly.

**Alternatives considered**:

- Change `util.py` to pass only the dataclass. This would satisfy new code but
  not preserve current in-repository caller behavior as required by FR-014.
- Keep the current seven-parameter method and add an internal dataclass helper.
  The public project-owned method would still violate the active parameter
  threshold.
- Move the method to `EventOverrides`. `util.py` and setup currently call through
  the coordinator because it owns `event_prefix` and refresh scheduling; moving
  the entry point would change caller behavior.

## Decision: Preserve reconciliation package integration unchanged

**Decision**: The coordinator continues using the package-root reconciliation
surface: `DesiredPlan`, `ManagedSlot`, `Reservation`, `SlotStatus`,
`compute_desired_plan`, `extract_booking_aliases`,
`make_reservation_fingerprint`, and `normalize_slot_name_for_fingerprint`.

**Rationale**: Feature 015 already decomposed reconciliation behind a package
compatibility boundary. FR-018 requires preserving that integration. Coordinator
helper modules prepare equivalent input data; they do not reimplement planning,
slot action selection, fingerprint algorithms, or alias extraction.

**Alternatives considered**:

- Move reservation matching into reconciliation. That would blur feature scopes
  and risk changing the already-merged reconciliation package behavior.
- Duplicate fingerprint or alias helpers in coordinator helpers. Duplication
  would invite drift and violate the public reconciliation surface requirement.

## Decision: Measure complexity before removing only the complexity directive

**Decision**: Keep the Home Assistant runtime import directive and remove only
`complexity/file-too-large complexity/function-too-long` after measuring all
coordinator-related files and functions against active thresholds.

**Rationale**: The source has two separate `aislop-ignore-file` directives. The
spec explicitly requires keeping `ai-slop/hallucinated-import` because Home
Assistant runtime imports are legitimate, and removing only the temporary
complexity suppression. Measuring immediately before deletion prevents the prior
analyze-gate failure mode where a suppression is removed before all resulting
files are actually under threshold.

**Alternatives considered**:

- Remove both directives. That violates FR-015 and would conflate legitimate HA
  runtime imports with complexity debt.
- Leave the complexity directive. That fails the maintainability goal and
  success criteria.
