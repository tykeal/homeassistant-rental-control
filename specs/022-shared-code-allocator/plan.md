<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Implementation Plan: Shared Door Code Allocator with Persisted Registry

**Feature**: `022-shared-code-allocator` | **Planning Branch**:
`022-shared-code-allocator-plan` | **Date**: 2026-09-16 | **Spec**:
[spec.md](spec.md)
**Input**: Feature specification from
`specs/022-shared-code-allocator/spec.md` and GitHub issue #743, including the
issue comment "Plan-stage implementation hazards (from spec review)"

## Summary

Add one per-Home-Assistant-system door code allocator that owns a persisted
registry of issued codes, and route every code a reservation receives through
it. A new `custom_components/rental_control/allocator/` package holds the
registry, the deterministic candidate sequence, the Home Assistant `Store`
wiring, and a singleton accessor on `hass.data[DOMAIN][ALLOCATOR]` built on the
same shape Keymaster uses for `hass.data[DOMAIN][COORDINATOR]`.

Allocation is spliced into the existing refresh pipeline at one point:
`CoordinatorRefreshMixin._run_reconciliation` already builds reservations
(`coordinator_helpers/reservations.py`) and then computes the desired plan.
A new step between those two calls adopts codes observed on locks, re-keys
allocations that reconciliation rematched, allocates codes for reservations
that still need one, and sweeps releasable allocations. Entries with no managed
lock run an allocation-only variant of the same step so their sensors keep
publishing codes (FR-023).

The calendar sensor stops generating. `_generate_door_code` and its fallback in
`_handle_event_update` are removed; `slot_code` comes from
`coordinator.get_slot_code(identity_key)` only (FR-019).

Three protective invariants carry the plan's risk:

1. A reservation whose code is unavailable holds its physical slot. It never
   produces `CLEAR`, `RESET`, `OVERWRITE_MANUAL_CHANGE`, or `SET`.
2. Allocation runs inside the first refresh, which already completes before
   platforms are forwarded, so sensors do not publish before allocation has run.
3. The allocator never emits reconciliation actions, so pre-existing duplicates
   are recorded and reported without competing with the `duplicate_non_canonical`
   path.

## Technical Context

**Language/Version**: Python >=3.14.2
**Primary Dependencies**: Home Assistant runtime >=2026.4.0 per `hacs.json`,
specifically `homeassistant.helpers.storage.Store`, config-entry lifecycle,
and `DataUpdateCoordinator`; dev/test dependency `homeassistant>=2026.6.0`;
test tooling `pytest-homeassistant-custom-component`
**Storage**: One new Home Assistant `Store` at key
`rental_control.code_registry`, schema version 1, shared by every config entry,
holding codes obfuscated at rest. The existing per-entry cache store
(`rental_control.slot_mappings.<entry_id>`, `STORE_SCHEMA_VERSION`) is unchanged
and keeps its no-PIN policy.
**Testing**: `uv run pytest tests/ -q -p no:randomly` and
`uv run ruff check custom_components/ tests/`; pre-commit for ruff-format,
mypy, interrogate, reuse, aislop, gitlint
**Target Platform**: Home Assistant custom integration on the HA asyncio event
loop
**Project Type**: Single Home Assistant custom integration
**Performance Goals**: Allocation is in-memory under one `asyncio.Lock` and
runs once per refresh per entry. Per cycle it is O(reservations) with an O(1)
registry lookup per reservation, degrading to a bounded walk only on collision.
The registry is saved through `Store.async_delay_save` so a refresh performs at
most one queued write, keeping the 30s minimum refresh interval intact.
**Constraints**: No new operator configuration (FR-024). No change to
`DEFAULT_CODE_LENGTH` or the configured length (FR-012). No door code in any
form in logs or diagnostics (FR-025). No physical lock write may be introduced or
removed by the absence of an allocation. 95% coverage floor. Files below 400
lines, functions below 80 lines, at most six parameters, no aislop suppression.
**Scale/Scope**: Ten or more config entries on one Home Assistant system
against one shared parent lock, four-digit code space (10,000 candidates),
tens of concurrent reservations.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I: Code Quality & Testing | PASS | New pure modules (registry, candidates) are directly unit-testable; the HA-facing surface is one singleton accessor and one refresh step. Test strategy below names the oracle suites and the new suites. |
| II: Atomic Commit Discipline | PASS | This PR is one docs-only PLAN commit. Implementation splits naturally into registry/candidates, store+singleton, refresh integration, reconciliation guards, sensor parity, and adoption/release. |
| III: Licensing & Attribution | PASS | All new markdown carries SPDX headers; future Python modules must too. |
| IV: Pre-Commit Integrity | PASS | No hook bypass. Quickstart defines the validation gate. |
| V: Agent Co-Authorship & DCO | PASS | PLAN commit uses `git commit -s` plus an AI co-author trailer. |
| VI: User Experience Consistency | PASS | No new config flow fields, no renamed entities, and `slot_code` keeps its attribute name and type contract of "string or absent". |
| VII: Performance Requirements | PASS | In-memory allocation, delayed store writes, no new I/O in the sensor path (the sensor does strictly less work than today). |

**Gate result: PASS** — no violations. Proceeding to Phase 0.

## Project Structure

### Documentation (this feature)

```text
specs/022-shared-code-allocator/
├── plan.md                        # This file
├── research.md                    # Phase 0 decisions and alternatives
├── data-model.md                  # Phase 1 entities, states, validation
├── quickstart.md                  # Phase 1 implementation/validation guide
├── contracts/
│   └── code-registry-store.md     # Persisted schema + allocator API contract
├── checklists/requirements.md     # Existing spec-stage checklist
└── tasks.md                       # Phase 2 output only; NOT created here
```

`contracts/` is present because this feature introduces a durable persisted
schema and an internal API consumed by more than one caller. No HTTP, WebSocket,
or Home Assistant service contract is added.

### Source Code (repository root)

```text
custom_components/rental_control/
├── allocator/
│   ├── __init__.py                # Public names: DoorCodeAllocator, accessors
│   ├── models.py                  # AllocationOwner, AllocationRecord, results
│   ├── registry.py                # Pure in-memory registry + (de)serialization
│   ├── candidates.py              # Deterministic full-cycle candidate sequence
│   ├── store.py                   # Store key/version, load, validate, save
│   ├── allocator.py               # Lock, allocate/adopt/release/sweep/rekey
│   ├── services.py                # clear_orphaned_codes registration/handler
│   └── singleton.py               # get-or-create on hass.data, teardown rules
├── coordinator_helpers/
│   ├── code_allocation.py         # Refresh-cycle allocation step (new)
│   └── coordinator_refresh_shell.py  # Calls the step; lockless path added
├── reconciliation/
│   ├── actions.py                 # Guard: no code -> hold, never overwrite
│   ├── desired.py                 # Guard: no code -> never SET/CLEAR
│   └── plan_models.py             # Reservation.slot_code becomes optional
├── sensors/
│   ├── calsensor.py               # Remove _generate_door_code + fallback
│   └── calsensor_helpers/
│       ├── slots.py               # Read allocated code for lockless entries too
│       └── codes.py               # Generation helper leaves the display path
├── services.yaml                  # clear_orphaned_codes declaration
├── strings.json                   # Service name/description/fields
├── translations/{en,fr}.json      # Service translations
└── const.py                       # ALLOCATOR, registry store key/version

tests/
├── unit/
│   ├── test_allocator_registry.py
│   ├── test_allocator_candidates.py
│   ├── test_allocator_store.py
│   ├── test_allocator_singleton.py
│   ├── test_allocator_services.py
│   └── test_code_allocation_step.py
└── integration/
    ├── test_allocator_cross_entry.py
    ├── test_allocator_adoption.py
    └── test_allocator_failclosed.py
```

**Structure Decision**: A dedicated `allocator/` package rather than more
mixins on the coordinator. The allocator outlives any single coordinator, is
shared by every entry, and must stay testable without Home Assistant. Only
`store.py` and `singleton.py` touch `hass`; `registry.py` and `candidates.py`
are pure. The coordinator reaches the allocator through one helper module,
`coordinator_helpers/code_allocation.py`, matching the existing shell pattern.

## Design Decisions

### 1. Singleton lifecycle (FR-001, FR-002, FR-003)

`allocator/singleton.py` exposes:

```python
async def async_get_or_create_allocator(hass) -> DoorCodeAllocator
def get_allocator(hass) -> DoorCodeAllocator | None
```

`async_get_or_create_allocator` mirrors Keymaster's
`_async_get_or_create_coordinator`: return `hass.data[DOMAIN][ALLOCATOR]` when
present, otherwise construct, `await allocator.async_load()`, store, and return.
Creation failure pops `hass.data[DOMAIN].pop(ALLOCATOR, None)` and re-raises so
`async_setup_entry` converts it to `ConfigEntryNotReady` (FR-002). Because two
entries can set up concurrently, creation is guarded by a module-level
`asyncio.Lock` stored alongside the allocator, and the "already present" check
is repeated after acquiring it.

`async_setup_entry` creates the allocator immediately after
`hass.data[DOMAIN] = {}` is ensured and **before**
`coordinator.async_load_slot_store()`, so the registry exists before any refresh
can request a code. It also calls
`allocator.register_entry(config_entry.entry_id)` there (see the adoption gate).

`async_unload_entry` does not touch the allocator or the registry; it only
removes `hass.data[DOMAIN][entry_id]` as it does today (FR-003). A reload
therefore re-adopts from the registry and rotates nothing.

Entry removal is a distinct hook. The plan adds `async_remove_entry` to
`__init__.py`, which calls `allocator.async_release_entry(entry_id)`; that
method releases only allocations whose code is not currently observed on any
managed lock and retains the rest as orphan records with a warning (FR-004).
Retained orphans are cleared by the operator through the
`rental_control.clear_orphaned_codes` service described in decision 7.

### 2. Where allocation runs in the refresh cycle

`_run_reconciliation` currently does: observe slots, build reservations, apply
check-in protection, compute plan, apply plan, sync store. The new step is
inserted after check-in protection and before `compute_desired_plan`:

```python
await code_allocation.async_resolve_codes(self, reservations, observed_slots)
```

The step runs entirely inside the allocator's lock in four ordered phases:

1. **Adopt** every observed code for this entry's managed slots, attributing it
   to the reservation matched to that slot (FR-020, FR-021).
2. **Rekey** allocations whose reservation was rematched, using
   `Reservation.fingerprint_history`, so a date or UID change keeps the code.
3. **Allocate** for reservations that still have no code, in a stable order
   (sorted by `identity_key`) so the outcome does not depend on feed order.
4. **Sweep** allocations owned by this entry whose reservation is no longer
   active, releasing only those whose code is not observed on a lock (FR-013,
   FR-014).

`_run_reconciliation` is already wrapped in a `try/except Exception` that skips
the cycle on failure; the allocation step keeps that property and must not raise
for ordinary conditions such as exhaustion, which are reported instead.

For entries with no managed lock (`event_overrides is None`), `_async_update_data`
gains a small branch that builds reservations with `managed_slots=None` and runs
phases 2 through 4 only (there is nothing to adopt), then sets
`self._latest_res_by_key` so `get_slot_code` works. It computes no plan, emits no
actions, and calls no services (FR-023).

### 3. Preferred code, collision resolution, determinism (FR-009 to FR-011)

The preferred code is whatever `codegen.generate_slot_code` already returns for
the entry's generator, including its `date_based` fallback. Nothing in
`codegen.py` changes; it simply stops being the last word. The reservation
builder keeps setting `slot_code` to the generated value, and the allocation
step treats that value as the *preferred* input rather than the final answer.

Resolution when the preferred code belongs to another reservation walks an
affine sequence over the code space:

- `n = 10 ** code_length` (every zero-padded string of the configured length;
  `date_based` already emits leading zeros today, so nothing is excluded)
- `seed = int(sha256(identity_key.encode()).hexdigest(), 16)`
- `start = seed % n`
- `step = a value derived from seed, forced odd and non-multiple of 5, so that
  gcd(step, n) == 1`
- candidate `i` is `f"{(start + i * step) % n:0{code_length}d}"`

Because `step` is coprime to `n`, the sequence visits all `n` codes exactly once
before repeating, giving FR-011's non-repeating full-space guarantee and a
deterministic result for a given identity and registry. Exhaustion is declared
only after all `n` candidates are rejected, and is reported once per cycle per
entry rather than per reservation (FR-008).

### 4. Registry, store key, and at-rest obfuscation (FR-005, FR-016, FR-025)

One store, `rental_control.code_registry`, version 1, shared by all entries.
The payload is a list of records keyed by the code, each with one or more
owners; two or more owners means an adoption conflict. Full schema is in
[contracts/code-registry-store.md](contracts/code-registry-store.md) and the
entity semantics are in [data-model.md](data-model.md).

Codes must be *recoverable* — FR-017 wants a code to survive a restart and
FR-019 wants the sensor to display it, and adopted or collision-resolved codes
cannot be re-derived — but they are not stored as bare digits. Each record holds
`encoded_code`, using the same scheme Keymaster applies to PINs in
`custom_components/keymaster/serialization.py`: base64 of the salt bytes
followed by the code bytes, decoded by stripping the salt's byte length. The
salt is the `entry_id` of the record's first owner, fixed for the record's
lifetime and recorded in `encoding_salt_source`.

**This is obfuscation, not encryption, and not a security boundary.** The salt
sits in the same file as the value it hides, so file access trivially recovers
every code. It buys exactly one thing: door codes do not appear as greppable
plaintext in `.storage` or in backup archives. Nothing in the design may treat
it as a protection against an attacker.

Encoding is at-rest only. In-memory values are plain, and collision detection
compares plain values; encode on save, decode on load, nowhere else.

Separately and for a different purpose, logs and diagnostics never contain a
code at all. They use `code_ref`, the first eight hex characters of
`sha256(code_ref_salt + code)`, where `code_ref_salt` is a random value
generated once and persisted with the registry (FR-025). The two salts are
unrelated and neither substitutes for the other.

Load failure, absence, or validation failure yields an empty registry plus a
warning and a persistent notification (FR-018); adoption then rebuilds it.

### 5. Sensor parity (FR-019)

`calsensor._handle_event_update` drops:

```python
if self._event_attributes["slot_code"] is None:
    self._event_attributes["slot_code"] = self._generate_door_code()
```

`_generate_door_code` is deleted along with its now-unused `DoorCodeRequest`
construction. `calsensor_helpers/codes.py` remains only if another caller needs
it; the display path no longer imports it. `slots.read_slot` currently only
consults the coordinator when `event_overrides_present` is true; that gate is
replaced by an unconditional `coordinator.get_slot_code(identity_key)` /
`get_slot_assignment(identity_key)` lookup, which is correct now that lockless
entries populate `_latest_res_by_key`. `slot_code` may be `None`, which the
attribute dict already permits.

`last_four` is unaffected. It is parsed from the description, not generated, so
downstream consumers that fall back to it keep working during any no-code
window.

### 6. Reconciliation safety guards

`Reservation.slot_code` becomes `str | None`. `None` means "no code is available
for this reservation this cycle" — either fail-closed per FR-018 or awaiting the
adoption gate. `code_source` gains `"allocated"`, `"collision_resolved"`,
`"adopted"`, and `"unallocated"`. The guards are in
[Hazard resolutions](#hazard-resolutions) below.

### 7. Orphan cleanup service (FR-004)

`rental_control.clear_orphaned_codes` releases allocations whose owning config
entry no longer exists. A service is not operator-supplied configuration under
FR-024 — that requirement targets values that must be kept consistent across
entries, the capacity constant that sank the rejected partitioning design — so
a manual operator action with no persisted setting is in scope.

Registered once in `allocator/services.py` from
`async_get_or_create_allocator`, guarded by `hass.services.has_service` so the
second config entry does not re-register it. It is a domain service, not an
entity service, because it has no entity target; the existing `checkout` and
`set_state` entity services on the sensor platform are untouched. Declared in
`services.yaml`, `strings.json`, and both `translations/en.json` and
`translations/fr.json`, matching the pattern those two services already follow.

Three properties matter:

- **FR-014 holds here too.** The service calls the same guard helper as
  `async_sweep` and `async_release_entry`; a code still programmed on a managed
  lock is refused, never released. The rule is shared, not reimplemented.
- **It reports both sides.** The response, the log line, and a persistent
  notification list what was cleared and what was refused with a reason
  (`code_still_programmed` or `adoption_conflict`), using `code_ref` only.
- **It is idempotent and always safe.** A `dry_run` field previews the outcome,
  and the operation takes the allocator lock like every other mutation, so it
  cannot race a refresh.

It stays narrow. It does not resolve conflicts, does not re-issue codes, and
must not grow toward #735's force-re-issue. The full contract is in
[contracts/code-registry-store.md](contracts/code-registry-store.md).

## Hazard resolutions

### Hazard 1 — "publish no code" must never clear a lock slot

The concrete mechanism is narrower and sharper than the issue comment could
know, and the plan names it: `reconciliation/actions.py` line 35 computes

```python
code_drift = ms.actual_code is not None and ms.actual_code != desired_res.slot_code
```

With `slot_code` absent, `code_drift` becomes true for every occupied matched
slot, yielding `OVERWRITE_MANUAL_CHANGE` and a rewrite with no code. That is the
lockout path, and it is reached before any `CLEAR` classification is considered.

Four guards close it:

1. **`classify_matched_desired_slot`**: if `desired_res.slot_code is None`,
   return `(ActionKind.NOOP, "code_unavailable")` before any drift computation.
   The slot is held exactly as it is. Date drift correction is deferred to the
   first cycle in which a code exists; holding a working code is strictly more
   important than a punctual date-range update.
2. **`desired._classify_slot`**: an occupied slot matched to a reservation
   remains matched whether or not a code was issued, because matching is by
   stable name and dates, never by code. The guard is made explicit: a slot whose
   `desired_identity_key` is not `None` may never be classified `stale`,
   `phantom`, `mis_assigned`, or `duplicate_non_canonical`. The allocator's
   silence is not evidence that a slot should be empty.
3. **`assign_unmatched_reservations`**: a reservation with `slot_code is None`
   is never assigned to a `FREE` slot. It is recorded as
   `plan.overflow[identity_key] = "code_unavailable"`, which produces no action
   and no write.
4. **`DesiredPlan.validate`**: a new invariant rejects any `SET`,
   `OVERWRITE_MANUAL_CHANGE`, or `UPDATE_TIMES` action whose desired reservation
   has no code, and `event_overrides.async_apply_plan` refuses to execute such an
   action defensively. Violations are logged like other invariant violations.

The symmetric guarantee on the allocator side already exists in FR-014 and is
implemented by the sweep: a code observed on any managed lock is never released,
so the registry cannot hand a live guest's code to somebody else while the
reconciliation planner is holding the slot.

Additionally, the allocator refuses to *issue new* codes for an entry that has
unreadable managed slots not accounted for by the registry. If a slot's code
cannot be read and the registry has no record covering that slot, the integration
cannot prove a new code is unique, so it fails closed for new reservations
(FR-018) while every existing code keeps working untouched.

### Hazard 2 — transient no-code window at startup

Verified rather than assumed, in two directions.

**Ordering inside Rental Control.** `async_setup_entry` performs
`await coordinator.async_config_entry_first_refresh()` *before*
`hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)`. The
allocation step runs inside that first refresh. Sensors are therefore created
after allocation has already happened, and their first published state carries
the allocated code. The allocator is created earlier still, before
`async_load_slot_store`. Under normal operation there is no window.

A window can remain in three bounded cases: reconciliation deferred for check-in
restore, a refresh that raised and skipped the cycle, and a genuinely new
reservation that arrives while the adoption gate is closed. In each, the code
appears on the next refresh.

**The adoption gate.** FR-020 requires adoption for all loaded entries to
complete before new codes are issued, which by itself would block first codes at
startup. The gate is scoped so that it never delays a code that already exists:

- At creation the allocator enumerates `hass.config_entries.async_entries(DOMAIN)`
  and records every non-disabled entry as pending adoption.
- An entry leaves the pending set when its first allocation step finishes.
- While the set is non-empty, **adoption, rekeying, and registry-known lookups
  proceed normally**; only issuance of a brand-new code is deferred, with
  `code_source="unallocated"` for that cycle.
- The gate also opens on a wall-clock deadline measured from allocator creation,
  after which issuance proceeds with a warning naming the entries that never
  reported. This prevents one broken entry from suppressing codes forever.

So restarts and upgrades — where every active reservation has either a registry
record or an observable lock code — see no window at all, and the deferred case
is limited to bookings that are new since the last refresh.

**Downstream verification.** `captive-portal` was read, not assumed, at
`addon/src/captive_portal/integrations/rental_control_service.py`. An event is
skipped only when `slot_name`, `slot_code`, and `last_four` are *all* missing
(line 270), and `slot_name` is always present for a real reservation, so a
missing `slot_code` does not drop the booking. `_get_auth_identifier` falls back
to `slot_code` then `slot_name` (lines 320-339). The repository upsert
(`persistence/rental_control_event_repository.py`, lines 51-62) overwrites
`slot_code` with `None` for the duration of the window and restores it on the
next poll; the booking row, its window, and any existing `AccessGrant` survive,
because grants are separate records keyed on `booking_ref`. A guest who types a
code during the window gets `BookingNotFoundError`, a retryable authentication
failure, not a revoked grant. That is graceful degradation, and combined with the
ordering above it is a window that should not occur in practice. No captive-portal
change is required by this feature.

### Hazard 3 — pre-existing duplicates versus `duplicate_non_canonical`

The two mechanisms operate on different axes and cannot fight, and the plan
makes that structural rather than incidental:

- The allocator's conflict is **code**-scoped and cross-entry: two reservations
  observed holding the same code string. Its response is to record both owners,
  mark the code unavailable to new allocations, warn, and raise a persistent
  notification. It emits no reconciliation action and never changes either
  reservation's `slot_code`, so both sides keep exactly the code they already
  have (FR-022).
- Reconciliation's `duplicate_non_canonical` is **slot-name**-scoped and
  single-entry: two physical slots matched to the same stable-name group within
  one entry's managed range (`desired.py`, `_record_matched_group`). It is
  decided entirely from names, dates, and slot occupancy, and never consults a
  code.

The only intersection is a slot cleared by the name-based path whose code the
registry still holds. That is handled by the sweep's existing rule: once the code
is no longer observed on any managed slot, its record is released on a later
cycle. Nothing needs to coordinate the two paths beyond that, and the hazard-1
guards ensure the allocator's silence never *creates* a duplicate or stale
classification.

Correcting a live duplicate remains #735's force-re-issue operation, exactly as
the spec says.

## Backwards compatibility and upgrade path

- First start after upgrade finds no registry, warns, and starts empty (FR-018).
  The first refresh of each entry adopts every readable lock code, so in-flight
  guest codes are recorded, not rotated (FR-020, SC-004).
- `_resolve_observed_code` in `coordinator_helpers/reservations.py` is unchanged
  and remains the source of observed codes; the allocation step consumes its
  result rather than replacing it.
- Lockless entries have no observable code. On upgrade their sensors keep
  showing the same code as before, because the generator's preferred code is
  still tried first (FR-010) and is unclaimed unless a lock-backed entry already
  adopted it, in which case a deterministic replacement is issued once and then
  persists.
- The per-entry cache store, its schema version, and its no-PIN policy are
  untouched. #736's "`slot_code` is never persisted" note in
  `plan_models.py` is superseded by the new registry and should be updated in
  the same commit that changes the field type.
- No config flow change, no options change, no entity rename, no new required
  option (FR-024, SC-007). The one new service is a manual operator action, not
  a setting, and nothing depends on it having been run.
- Downgrading to a prior release leaves an unused `rental_control.code_registry`
  file behind, which is harmless; codes revert to per-entry generation.

## Test strategy

Commands: `uv run pytest tests/ -q -p no:randomly` and
`uv run ruff check custom_components/ tests/`. The suite is around 1600 tests
with a 95% coverage floor, so new modules need tests in the same commit.

Known flake to expect and not chase:
`tests/integration/test_refresh_cycle.py::test_missing_store_adopts_coded_slots_when_unavailable_at_setup`.
Re-run it in isolation before treating it as a regression.

New unit coverage:

- `registry.py`: allocate/lookup/release, conflict recording, round-trip
  serialization, rejection of malformed payloads.
- `candidates.py`: full-cycle non-repetition for lengths 4 and 6, determinism
  for a fixed identity, and stability when the registry changes around it.
- `store.py`: missing, unreadable, wrong-version, and corrupt payload all yield
  an empty registry plus a warning; delayed save is used; encode/decode round
  trips exactly, including leading zeros, and no saved payload contains a bare
  code (assert the serialized JSON does not contain the digit string).
- `singleton.py`: second entry reuses the first allocator; creation failure pops
  the key; unload of one entry leaves the allocator intact.
- `services.py`: the service registers once across two entries; `dry_run`
  changes nothing; a code still on a lock is retained with
  `code_still_programmed`; a conflict record is retained with
  `adoption_conflict`; a second call is a no-op; no response field or log line
  contains a code.
- `code_allocation.py`: adopt-before-allocate ordering, idempotency on repeat
  requests, rekey via `fingerprint_history`, sweep retention for observed codes,
  exhaustion reporting.

New integration coverage:

- Two entries, identical dates, `date_based`: distinct codes, both registered
  (SC-001).
- Restart and single-entry reload: codes identical before and after (SC-005).
- Empty registry with coded slots: adoption, zero rotation (SC-004).
- Fail-closed: a reservation with no code produces no `CLEAR`, no `RESET`, no
  `OVERWRITE_MANUAL_CHANGE`, and no `SET` for its slot, and the physical code is
  still present at the end of the cycle. This is hazard 1's regression test and
  is mandatory.
- Pre-existing duplicate: both retained, conflict reported, code unavailable to
  new allocations.

Existing suites to update rather than delete: `tests/unit/test_calsensor_codes.py`
and any test asserting the sensor regenerates a code. Sensor tests become parity
tests against the coordinator's allocated code.

## Phase 0 Research Output

See [research.md](research.md). All planning questions are resolved; no open
clarifications remain.

## Phase 1 Design Output

See [data-model.md](data-model.md) for entities, states, and validation rules,
[contracts/code-registry-store.md](contracts/code-registry-store.md) for the
persisted schema, the internal allocator API, and the cleanup service, and
[quickstart.md](quickstart.md)
for the implementation and validation guide. Agent-context updates are omitted
because no new language, framework, dependency, or tool is introduced.

## Post-Design Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I: Code Quality & Testing | PASS | Pure registry and candidate modules carry the logic; HA coupling is confined to `store.py`, `singleton.py`, and one refresh step. |
| II: Atomic Commit Discipline | PASS | Six natural implementation commits are identified above. |
| III: Licensing & Attribution | PASS | SPDX headers on every new file. |
| IV: Pre-Commit Integrity | PASS | No bypass; quickstart gates on the full hook run. |
| V: Agent Co-Authorship & DCO | PASS | Sign-off plus co-author trailer. |
| VI: User Experience Consistency | PASS | `slot_code` keeps its name and may already be `None` in the attribute dict; no config or entity changes. |
| VII: Performance Requirements | PASS | One in-memory pass per refresh, delayed store writes, less work in the sensor than today. |

**Gate result: PASS** — no plan-stage constitution violations.

## Complexity Tracking

No constitutional violations require justification.

One storage policy point is recorded rather than hidden. The new registry
persists door codes in a recoverable form, where the per-entry cache store
persists none at all. Recoverability is forced by FR-017 and FR-019, since
adopted and collision-resolved codes cannot be re-derived. At rest the value is
obfuscated with Keymaster's salted base64 scheme, which keeps codes out of
plaintext in `.storage` and in backups. It is **not** encryption and **not** a
security boundary: the salt lives in the same file, so anyone who can read the
file can recover every code. It is worth doing because casual exposure is the
realistic risk, and it is worth being honest that it stops nothing else. Logs
and diagnostics are handled separately and more strictly: they carry `code_ref`
and never a code, encoded or otherwise (FR-025).

Both previously open points are now decided by the maintainer and folded into
the plan: FR-004's operator recovery path is the
`rental_control.clear_orphaned_codes` service (design decision 7), and a service
is not "configuration" under FR-024. No `[NEEDS CLARIFICATION]` markers remain.

## Phase Notes

- PLAN stage stops here. Do not create `tasks.md` and do not modify production
  code in this PR.
- Live source on `main` is the truth. Where this plan and the code disagree,
  re-read the code and adjust the plan.
- Settled spec decisions are not reopened. Per-instance partitioning stays
  rejected, code length stays out of scope, and force re-issue stays with #735.
