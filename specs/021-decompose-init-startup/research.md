<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Research: Decompose Init Startup Readability

## Decision: Keep `__init__.py` as the Home Assistant entry shell

**Rationale**: Home Assistant loads the integration through the package module,
and the current source owns `async_setup_entry`, `async_unload_entry`,
`update_listener`, and `async_start_listener` there. Visible tests patch
`custom_components.rental_control.async_start_listener` while exercising
`update_listener`, so listener restart must continue to resolve the package
module global at runtime. Keeping the shell in place preserves setup ordering,
unload cleanup, update-listener behavior, package imports, and the normal state
listener patch seam.

**Alternatives considered**:

- Move all entry lifecycle functions to a helper module and re-export them:
  rejected because hidden tests and Home Assistant lifecycle imports are safest
  when the entry shell remains the package module.
- Move `async_start_listener` with startup readability: rejected because tests
  patch the package-level name, and startup readability is separate from normal
  Keymaster state-change handling.

## Decision: Extract startup readability to `startup_readability.py`

**Rationale**: The remaining complexity concern is self-contained: managed slot
entity discovery, readable-state checks, startup unreadability decision, state
watching, debounce, watchdog, one-shot coordinator refresh, and cleanup. A
single sibling module mirrors the established #572 extraction pattern where
`migrations.py` and `listeners.py` took focused entry concerns out of
`__init__.py` while package-level re-exports preserved callers.

**Alternatives considered**:

- Leave helpers in `__init__.py` and only move the long function: rejected
  because the startup-readability concern includes the four module-level helpers
  and keeping them in the shell weakens the file-size fix.
- Add a top-level generic helpers module: rejected because these helpers are
  specific to startup Keymaster readability and should not become general API.
- Split into multiple startup modules immediately: rejected because one focused
  module remains below thresholds and avoids unnecessary import surface.

## Decision: Re-export `async_arm_startup_readability_refresh` from the package

**Rationale**: Visible tests import the arming function from
`custom_components.rental_control` and call it directly for missed-transition
coverage. The public function already has an acceptable four-parameter
signature. Importing it into `__init__.py` from `startup_readability.py` keeps
that package-level import and direct call stable while allowing the
implementation to move.

**Alternatives considered**:

- Require tests and callers to import from `startup_readability`: rejected
  because the feature is behavior-preserving and must keep package-level caller
  compatibility.
- Add a wrapper in `__init__.py` with duplicate logic: rejected because it risks
  stale behavior and does not reduce the shell as much as a direct re-export.

## Decision: Import the private needs helper for setup ordering

**Rationale**: `async_setup_entry` currently records startup unreadability before
the first coordinator refresh and passes that fact into the arming function after
the refresh. The helper that returns `(needs_refresh, entity_ids)` should move
with the rest of startup readability, but `__init__.py` still needs to call it at
the same point in setup. Importing `_needs_startup_readability_refresh` from the
new module preserves ordering without duplicating state discovery.

**Alternatives considered**:

- Have `async_arm_startup_readability_refresh` infer all state after the first
  refresh: rejected because it would lose the startup-unreadable fact required
  for missed-transition behavior.
- Store startup unreadability in coordinator state: rejected because it adds
  state to an unrelated object and changes the current data flow.

## Decision: Use a private watcher object for nested callback state

**Rationale**: The 143-line function is long because eight nested callbacks share
`done`, unsubscribe handles, and the refresh task through `nonlocal` variables.
A private slots dataclass or class can hold that lifecycle state and expose short
methods for arming, cleanup, debounce scheduling, refresh, and expiry. This
keeps the public function below 80 lines while making cancellation and one-shot
state explicit.

**Alternatives considered**:

- Promote every closure to a module-level function and pass all handles around:
  rejected because handle mutation would require awkward mutable containers or
  too many parameters, risking the six-parameter limit.
- Keep nested callbacks but add a suppression directive: rejected by the spec and
  constitution; findings must be resolved rather than hidden.
- Use `functools.partial` for all callbacks without an object: rejected because
  lifecycle state still needs a coherent owner for cleanup and task references.

## Decision: Preserve exact one-shot, debounce, watchdog, and cleanup semantics

**Rationale**: Startup readability protects lock-code reconciliation during Home
Assistant startup. Behavior drift could skip the corrective refresh, schedule it
more than once, or allow stale callbacks after unload. The watcher methods must
preserve current readiness tests, old-state/new-state filtering, timer
replacement, `done` transitions, refresh task naming, refresh error logging,
listener-list removal, and watchdog expiry behavior.

**Alternatives considered**:

- Refresh immediately when all entities are readable: rejected because current
  behavior applies the debounce delay even for missed transitions.
- Remove the watchdog after extraction: rejected because current behavior gives
  up and removes the cleanup callback after ten minutes.
- Leave completed watcher cleanup to unload only: rejected because current
  `refresh_done` removes the cleanup reference when the one-shot task finishes.

## Decision: Keep normal listener startup in `__init__.py`

**Rationale**: `async_start_listener` is not part of the startup-readability
watcher. It tracks all normal Keymaster entities and delegates to
`handle_state_change`. Visible tests patch
`custom_components.rental_control.async_start_listener`, and `update_listener`
expects that patch to intercept listener restart. Keeping the function in the
package shell avoids changing that patch seam.

**Alternatives considered**:

- Move normal listener startup to `listeners.py`: rejected because #572 extracted
  Keymaster event-bus monitoring, not this state-change listener, and moving it
  now would broaden the feature scope.
- Import the function from a helper and call the helper directly from
  `update_listener`: rejected because it could bypass package-path patches.

## Decision: Omit contracts and agent-context updates

**Rationale**: This is an internal, behavior-preserving refactor plan. It adds no
external API, service schema, event payload, entity platform, storage format,
runtime dependency, language, framework, package manager, or tool. Existing Home
Assistant-visible behavior and package-level Python import surfaces are
preserved rather than extended.

**Alternatives considered**:

- Add contract files for internal watcher methods: rejected because
  `data-model.md` and `plan.md` already describe internal lifecycle entities.
- Run `update-agent-context.sh`: rejected because no new technology or dependency
  is introduced.
