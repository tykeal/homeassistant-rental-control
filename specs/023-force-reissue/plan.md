<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Implementation Plan: Force Re-Issue of a Door Code

**Feature**: `023-force-reissue` | **Planning Branch**:
`023-force-reissue-plan` | **Date**: 2026-09-17 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/023-force-reissue/spec.md` and
GitHub issue #735, discharging feature 022's FR-022 reference to #735 as the
owner of correcting an existing duplicate.

## Summary

Add one operator-invoked Home Assistant service,
`rental_control.force_reissue`, that acts on exactly one target and makes the
*next* reconcile cycle issue that target a fresh code through the shared
allocator delivered by feature 022.

The service itself writes nothing to a lock. It resolves and validates one
target, records an in-memory **pending re-issue** on that target's coordinator,
and lets the existing refresh pipeline do the work. That keeps FR-011, FR-015,
and FR-016 structural rather than aspirational: there is no second code path,
no second write path, and no generator call outside the reservation builder.

Three mechanisms carry the feature, and each one was verified against live
source on `main` at `a9b82f9` rather than inferred from a document:

1. **One-cycle retention suppression.** `_resolve_observed_code`
   (`coordinator_helpers/reservations.py:224`) returns
   `(observed_code, "manual_observed")` whenever the observed PIN is not what
   the generator would produce. A pending re-issue suppresses that return for
   exactly one target for exactly one cycle, so the freshly generated preferred
   code survives into the allocation step. The same cycle also suppresses the
   *allocator-side* form of retention: the target's `AdoptionRequest`, which
   would otherwise re-adopt the old physical code straight back onto the same
   identity and make the whole operation a no-op.

2. **A held replaced code, not a released one.** The target identity currently
   owns the old code in the registry, and `AllocationRegistry.by_identity`
   permits one code per identity, so the identity must stop owning the old code
   before it can be issued a new one. The forced re-issue therefore **re-homes**
   that one owner onto a purpose-built *forced-release hold* identity on the
   same record. The record keeps an owner, so the old code stays unavailable to
   every other allocation (FR-020) and survives a restart, while the real
   identity is free to allocate (FR-014).

3. **A narrowly-typed guard exemption.** The hold is released only through
   `DoorCodeAllocator._release_guard_reason`, with a single exemption that
   disarms the `adoption_conflict` condition for that one owner and leaves both
   physical-state conditions — `unverifiable_lock` and `code_still_programmed`
   — fully in force (FR-019). See
   [The FR-019 guard exemption](#the-fr-019-guard-exemption).

Two requirements need **no code at all**, which is the single most valuable
result of reading the source before writing this plan:

- **FR-026 is already satisfied.** `RentalControlCoordinator.get_slot_code`
  (`coordinator.py:200-216`) already publishes the *observed* code for a
  lock-backed entry and only switches to the reservation's code once
  `observed_code == res.slot_code`, and it already returns `None` when
  `observed_slot != selected_slot`. That is exactly "keep publishing the last
  confirmed code until the new write is physically confirmed", already keyed by
  slot so a stale code cannot leak across a slot reassignment. The lockless
  branch returns `res.slot_code` immediately, which is FR-016. No sensor
  attribute is added, removed, or changed (FR-026, second sentence).
- **FR-015 is already satisfied.** Once the target's reservation carries the new
  code and the slot still holds the old one,
  `reconciliation/actions.py:classify_matched_desired_slot` computes
  `code_drift` and returns `OVERWRITE_MANUAL_CHANGE` with
  `"drifted fields: code"`. The ordinary plan writes the replacement. No
  direct write, and no change to `reconciliation/`.

## Technical Context

**Language/Version**: Python >=3.14.2
**Primary Dependencies**: Home Assistant runtime >=2026.4.0 per `hacs.json`;
`homeassistant.core.ServiceCall` / `SupportsResponse`,
`homeassistant.exceptions.ServiceValidationError`,
`homeassistant.helpers.config_validation`, `DataUpdateCoordinator`; dev/test
dependency `homeassistant>=2026.6.0`; test tooling
`pytest-homeassistant-custom-component`
**Storage**: **No new store, no new schema, and no schema version bump.** The
feature reuses the feature 022 registry at `rental_control.code_registry`
(version 1) exactly as it is. The forced-release hold is an ordinary
`AllocationOwner` whose `identity_key` uses a reserved namespace, in the same
way `issuance.observed_alias_key` already writes
`"<key>:observed:<entry>:<lock>:<slot>"` owners today. The one-cycle
suppression is in-memory only and is never persisted (FR-013).
**Testing**: `uv run pytest tests/ -q -p no:randomly` and
`uv run ruff check custom_components/ tests/`; pre-commit for ruff-format,
mypy, interrogate, reuse, yamllint, gitlint
**Target Platform**: Home Assistant custom integration on the HA asyncio event
loop
**Project Type**: Single Home Assistant custom integration
**Performance Goals**: One service invocation performs O(1) registry work under
the existing `asyncio.Lock`, plus one coordinator refresh that the integration
would have run anyway. The per-cycle forced-hold release pass is
O(records × owners) over a registry that is already walked once per cycle by
`_sweep_unlocked`, so cycle cost is unchanged in order and negligible in
constant.
**Constraints**: No new operator configuration (FR-027). No new persisted state
(FR-013). No new or changed calendar sensor attribute (FR-026). No bulk mode
(FR-004). Raw codes only in the dry-run response (FR-023); `code_ref` everywhere
else (FR-022, FR-024). 95% coverage floor. Files below 400 lines, functions
below 80 lines, at most six parameters, no aislop suppression.
**Scale/Scope**: The live motivating deployment is ten config entries against
one shared parent lock with carved-out slot ranges, a four-digit code space, and
one duplicated code across two entries.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I: Code Quality & Testing | PASS | The guard exemption, the hold key helpers, and target resolution are pure and unit-testable without Home Assistant. The HA-facing surface is one domain service handler. Test strategy names the mandatory regression and the end-to-end healing test. |
| II: Atomic Commit Discipline | PASS | This PR is one docs-only PLAN commit. Implementation splits into: guard exemption + hold model; cycle wiring; suppression in the reservation builders; service registration and schema; dry-run preview; reporting and diagnostics. |
| III: Licensing & Attribution | PASS | Every new markdown file carries the SPDX header; future Python modules must too. |
| IV: Pre-Commit Integrity | PASS | No hook bypass; quickstart defines the validation gate. |
| V: Agent Co-Authorship & DCO | PASS | PLAN commit uses `git commit -s` plus an AI co-author trailer. |
| VI: User Experience Consistency | PASS | No config flow field, no entity rename, no attribute change. One new service declared in `services.yaml`, `strings.json`, and both translations, exactly like `clear_orphaned_codes`. |
| VII: Performance Requirements | PASS | No new I/O in the sensor path, no new store, one extra bounded registry walk per cycle. |

**Gate result: PASS** — no violations. Proceeding to Phase 0.

## Project Structure

### Documentation (this feature)

```text
specs/023-force-reissue/
├── plan.md                          # This file
├── research.md                      # Phase 0 decisions and alternatives
├── data-model.md                    # Phase 1 entities, states, validation
├── quickstart.md                    # Phase 1 implementation/validation guide
├── contracts/
│   └── force-reissue-service.md     # Service contract + allocator API delta
├── checklists/requirements.md       # Existing spec-stage checklist
└── tasks.md                         # Phase 2 output only; NOT created here
```

`contracts/` is present for the same reason it was in feature 022: this feature
introduces a Home Assistant service with a service response, and an internal
allocator API delta consumed by more than one caller. It introduces **no**
persisted schema change, so the contract explicitly records that the store
shape is untouched.

### Source Code (repository root)

```text
custom_components/rental_control/
├── allocator/
│   ├── reissue.py                 # NEW: hold identity, directive application,
│   │                              #      guarded hold release, read-only preview
│   ├── reissue_service.py         # NEW: force_reissue registration + handler
│   ├── models.py                  # MOD: ForcedReissueDirective,
│   │                              #      ForcedReleaseExemption, ReissuePreview,
│   │                              #      ReissueOutcome; CycleRequest/CycleResult
│   ├── allocator.py               # MOD: guard exemption parameter, sweep skip,
│   │                              #      async_preview_reissue entrypoint
│   ├── issuance.py                # MOD: extract select_code(); call the reissue
│   │                              #      and hold-release steps in resolve_cycle
│   ├── adoption.py                # MOD: suppress identity-mismatch reporting for
│   │                              #      a slot that already carries a hold
│   └── services.py                # MOD: register the new service alongside
│                                  #      clear_orphaned_codes
├── coordinator_helpers/
│   ├── reissue.py                 # NEW: PendingReissue, target resolution,
│   │                              #      per-coordinator pending state
│   ├── reservations.py            # MOD: _resolve_observed_code suppression
│   ├── checkin_protection.py      # MOD: second manual_observed site
│   ├── models.py                  # MOD: ReservationBuildContext gains the
│   │                              #      suppression value object
│   ├── code_allocation.py         # MOD: skip suppressed adoptions, keep
│   │                              #      _adoption_complete honest, pass
│   │                              #      directives, surface outcomes
│   ├── coordinator_setup_shell.py # MOD: _reservation_build_context passes
│   │                              #      the pending suppression
│   └── coordinator_refresh_shell.py  # MOD: consume the pending re-issue once
├── services.yaml                  # MOD: force_reissue declaration
├── strings.json                   # MOD: name/description/fields
└── translations/{en,fr}.json      # MOD: service translations

tests/
├── unit/
│   ├── test_allocator_reissue.py          # hold model, directive, preview
│   ├── test_allocator_release_guard.py    # exemption scope + ordinary paths
│   ├── test_reissue_service.py            # schema, targeting, guards, response
│   ├── test_reissue_suppression.py        # one-cycle retention suppression
│   └── test_reissue_targets.py            # entity/slot resolution and refusals
└── integration/
    ├── test_reissue_duplicate_healing.py  # the end-to-end US1 scenario
    ├── test_reissue_deferred_release.py   # FR-020/FR-021 intermediate state
    └── test_reissue_lockless.py           # FR-016 immediate publish
```

**Structure Decision**: The split follows feature 022's boundary exactly. The
registry-facing half (`allocator/reissue.py`) is pure: it takes a registry, a
record, owners, and observations, and imports no Home Assistant. The
Home-Assistant-facing half is the service module and the coordinator helper,
which own target resolution, `hass.data` lookups, and the response. New logic
goes in the pure half unless it genuinely needs `hass`. No new mixin is added to
the coordinator; the pending state lives in one helper module in the same shape
as the other `coordinator_helpers/` modules.

## The FR-019 guard exemption

This is the load-bearing design element of the feature, so it is specified
concretely rather than by intent.

### What the guard does today (verified, not assumed)

`custom_components/rental_control/allocator/allocator.py`:

```python
def _release_guard_reason(
    self,
    record: AllocationRecord,
    owners: list[AllocationOwner],
    observations: list[CycleObservation],
    *,
    refresh_observed: bool = True,
) -> str | None:
    if len(record.owners) > 1:
        return "adoption_conflict"
    for owner in owners:
        covered_observations = [...]
        if owner.lockname is not None and not covered_observations:
            return "unverifiable_lock"
        if self._owner_still_programmed(...):
            return "code_still_programmed"
    return None
```

It has exactly three callers, all in the same file: `_sweep_unlocked`,
`async_mark_entry_removed`, and `async_clear_orphans`. Each passes a
single-element `owners` list.

`AllocationRegistry.release(identity_key)`
(`allocator/registry.py:146-158`) removes **one** owner from a record,
pops that identity from `by_identity`, and deletes the record only when no
owners remain. Releasing one side of a two-owner record therefore leaves the
other side's ownership — and the code's unavailability — completely intact.

### Why an exemption is unavoidable

`adoption_conflict` fires on `len(record.owners) > 1`. A duplicate *is* two
owners of one code. Without an exemption the healing release can never happen,
and the feature cannot do the one thing it exists to do.

### The mechanism

Add a frozen, slotted, purpose-built value object in `allocator/models.py`:

```python
@dataclass(frozen=True, slots=True)
class ForcedReleaseExemption:
    """Names the single owner one forced re-issue deliberately re-homed."""

    code: str
    entry_id: str
    identity_key: str
```

and one keyword-only parameter on the guard, defaulting to "no exemption":

```python
def _release_guard_reason(
    self,
    record: AllocationRecord,
    owners: list[AllocationOwner],
    observations: list[CycleObservation],
    *,
    refresh_observed: bool = True,
    forced_release: ForcedReleaseExemption | None = None,
) -> str | None:
    if len(record.owners) > 1 and not _conflict_exempt(
        record, owners, forced_release
    ):
        return "adoption_conflict"
    # ... unchanged from here down ...
```

`_conflict_exempt` lives in `allocator/reissue.py`, is pure, and returns `True`
only when **every** one of these holds:

1. `forced_release is not None`;
2. `len(owners) == 1` — an exemption can only ever describe one owner, never a
   record;
3. `owners[0].identity_key == forced_release.identity_key`;
4. `owners[0].entry_id == forced_release.entry_id`;
5. `record.code == forced_release.code`;
6. `is_forced_release_hold(owners[0].identity_key)` — the owner is in the
   reserved hold namespace, so a live reservation identity can never be
   exempted even if an exemption naming it were somehow constructed.

Anything less than all six falls through to the full unmodified guard. A stale
or mismatched exemption is inert, not dangerous.

### Why it cannot be triggered accidentally

- **It is not a boolean.** There is no `force=True` to pass by mistake and no
  truthy value that enables it. The caller must construct a dedicated frozen
  dataclass, and mypy rejects anything else.
- **It is keyword-only with a `None` default.** No positional argument can drift
  into it, and the three ordinary call sites pass nothing, so they are byte-for-
  byte unchanged and provably keep the full guard.
- **It must match three fields exactly** against the single owner being
  evaluated, so an exemption built for one owner cannot leak onto another owner
  of the same record, or onto the same identity on a different code.
- **It only ever exempts a hold-namespace owner** (condition 6). The namespace
  is written only by the forced re-issue directive path.
- **It is constructed in exactly one place**: `reissue.release_forced_holds`,
  which builds it *from* the owner it is about to evaluate. There is no other
  producer, which a test asserts by grepping the production tree.

### What stays in force, and why that matters more than the exemption

`unverifiable_lock` and `code_still_programmed` are **not** exempted, for the
targeted owner or for anyone else. Releasing a code that is still programmed
would return it to the pool while it still opens a door, letting a different
entry be issued that same value and recreating the exact duplicate this feature
exists to fix. `SlotStatus.UNKNOWN` means *unreadable*, not empty — it is
produced at two sites in `coordinator_helpers/keymaster_observation.py`, once
with `blocked_reason="unreadable"` and once when `name_state` or `pin_state` is
`None` with no `blocked_reason`, so the only correct test is
`status is SlotStatus.UNKNOWN`. `SlotStatus.FREE` is known-empty and must never
be counted as unreadable. `build_cycle_observation` already encodes both rules;
this feature reuses it unchanged.

### The resulting normal timeline

For a lock-backed target, release legitimately **defers**, usually for at least
one cycle:

| Cycle | Registry | Physical slot | Guard verdict |
|-------|----------|---------------|---------------|
| N (service call + refresh) | identity re-homed to hold; identity allocated a new code | still the **old** code | `code_still_programmed` — retained |
| N, later in the same cycle | — | plan emits `OVERWRITE_MANUAL_CHANGE`, writes the new code | — |
| N+1 | hold still present | slot now reads the **new** code | `adoption_conflict` exempted, lock covered, old code not observed → **released** |

If the write fails, the lock goes unavailable, or the slot reads
`SlotStatus.UNKNOWN`, the hold simply persists and is retried next cycle
(FR-021). That is the FR-020 intermediate state, and it is a correct outcome,
not a fault. Throughout it, the old code remains registry-held and therefore
unavailable to every other allocation.

For a **lockless** entry the hold owner has `lockname is None`, so
`unverifiable_lock` is skipped and `_owner_still_programmed` returns `False`
immediately: the release completes in the same cycle. That is verified against
`_owner_still_programmed`'s first branch, not assumed.

## Design decisions

### 1. The service shape

`rental_control.force_reissue`, registered once for the domain in
`allocator/reissue_service.py` and guarded by `hass.services.has_service` so a
second config entry does not re-register it, with
`SupportsResponse.OPTIONAL`.

It is a **domain** service that takes an `entity_id` *field*, not an entity
platform service. That is a deliberate composition of the two precedents the
spec names, and it is forced by FR-002 and FR-003: a Home Assistant entity
service with a `target:` block cannot express "a lock and a slot instead of an
entity", and cannot be invoked with *neither* target so that the call can be
refused. `clear_orphaned_codes` already establishes the domain-service plus
`dry_run` plus response shape in this integration, and the `entity_id` field
uses an entity selector restricted to `integration: rental_control`,
`domain: sensor`, so the operator-facing targeting experience matches `checkout`
and `set_state`. The existing entity services are untouched.

Fields: `entity_id` (optional), `lockname` + `slot` (optional pair), `force`
(bool, default `false`), `dry_run` (bool, default `false`). No `retire` field,
ever (spec Out of Scope). Full schema, validation order, refusal reasons, and
response shape are in
[contracts/force-reissue-service.md](contracts/force-reissue-service.md).

**Single target is structural.** The schema accepts a single entity id, not a
list, and rejects `area_id`, `device_id`, and `entity_id: all`. There is no
wildcard and no "fix all conflicts" mode (FR-004).

### 2. Target resolution

`coordinator_helpers/reissue.py` resolves a request to a `ReissueTarget`
carrying `entry_id`, `identity_key | None`, `lockname | None`, and `slot | None`.

- **Entity form.** The entity must be a `RentalControlCalSensor` belonging to a
  loaded entry and must currently carry an event. Its identity is derived with
  the same call the sensor itself uses in
  `sensors/calsensor_helpers/slots.py:read_slot`:
  `make_reservation_fingerprint(entry_id, slot_name, start, end)`. Any other
  entity — including the check-in tracking sensor — is refused.
- **Slot form.** The entry is the loaded coordinator whose `lockname` matches
  and whose managed range contains the slot. That range is
  `range(coordinator.start_slot, coordinator.start_slot + coordinator.max_events)`,
  the same expression `coordinator_setup_shell.py:92` iterates. A slot outside
  every matching entry's range is refused (FR-005); a slot claimed by more than
  one matching entry is refused as ambiguous rather than guessed. If a live
  reservation currently occupies that slot, its identity is attached to the
  target so the operation behaves identically to the entity form; if none does,
  the target is a bare lock and slot (the US4 ghost).

### 3. Pending re-issue state (FR-013, FR-009)

`PendingReissue` is stored in a `dict[str, PendingReissue]` on the coordinator,
keyed by `identity_key` or by `f"slot:{lockname}:{slot}"`. It is a plain
attribute on a live object: **in memory only, never written to any store, and
gone after a Home Assistant restart**, exactly as FR-013 requires and as the
spec's edge case documents. The operator simply invokes the service again.
A second in-memory dictionary keeps completed target fingerprints for the same
runtime so an immediate automation retry after the hold has been released still
returns the prior outcome instead of rotating again. This is not persisted
suppression state and lapses on restart with the rest of the service state.

It carries `suppress_pending: bool`, cleared by the first reconcile cycle that
consumes it — that is the "next cycle only, this target only" guarantee — and a
lifecycle phase used for idempotency and reporting. The record itself survives
past the suppression, until the replaced code's hold is released, because that
is what makes FR-009 enforceable:

- A repeat call while a pending re-issue exists for the target is **not** a
  second rotation. It returns the original outcome and changes nothing.
- A repeat call while a forced-release hold still exists for that entry, lock,
  and slot is refused for the same reason, which covers the case where the
  in-memory record was lost to a restart but the registry hold was not.
- Once the hold is released and the record cleared, an identical invocation in
  the same runtime is matched against the completed fingerprint and reported as
  already completed. A later invocation after the target's observed code,
  reservation window, or Home Assistant runtime has changed is a new operator
  decision.

There is no timer and no persisted marker. While the hold exists it is the
durable idempotency key; after release, the in-memory completed fingerprint
covers immediate same-runtime retries.

### 4. Suppressing retention for one cycle

`ReservationBuildContext` (`coordinator_helpers/models.py:90`) gains one field,
a frozen `ReissueSuppression` value object holding the suppressed
`identity_keys: frozenset[str]` and `slots: frozenset[int]` for this cycle,
defaulting to an empty value so every existing construction site and every
existing test keeps working. `_reservation_build_context`
(`coordinator_helpers/coordinator_setup_shell.py:273`) fills it from the
coordinator's pending re-issues.

Three retention sites are suppressed for the targeted cycle, and **only** for
the named target:

1. **`_resolve_observed_code`** (`coordinator_helpers/reservations.py:224`).
   When the reservation's identity or its matched physical slot is suppressed,
   the function returns the caller's freshly generated `(slot_code,
   code_source)` instead of `(observed_code, "manual_observed")`. Everything
   else about the function is unchanged, so every non-targeted reservation keeps
   full retention (spec Out of Scope: "Changing the default retention
   behaviour").
2. **`checkin_protection.build_protected_reservation`
   (`coordinator_helpers/checkin_protection.py:49`)**. This is the *second*
   `manual_observed` site in the tree — it synthesizes a protected reservation
   for a checked-in guest whose booking is missing from the feed, pinning
   `slot_code` to the observed code. Its caller,
   `_synthesize_checkin_reservation`, currently chooses
   `matched_physical.actual_code` before the helper runs, so the suppression is
   checked at the caller as well: when the matched physical slot is suppressed,
   the caller passes the freshly generated code into
   `build_protected_reservation` instead of the observed code. It only fires
   when the calendar match failed, so it is reachable only by slot targeting,
   but leaving it unsuppressed would silently defeat a forced re-issue against
   exactly the kind of stuck target this feature exists for. The suppression is
   threaded to it through the same value object.
3. **The adoption request** (`code_allocation.build_adoption_requests`). Without
   this, the allocator re-adopts the observed old code onto the target identity
   in the same cycle, `allocate_request` returns the existing record
   idempotently, and nothing changes. The suppressed target contributes no
   `AdoptionRequest`.

**Trap, recorded so it is not rediscovered in production**: skipping an adoption
request naively breaks `_adoption_complete`
(`coordinator_helpers/code_allocation.py:151`), which requires
`readable_coded_slots <= adopted_slots`. A skipped target would leave a readable
coded slot unadopted, `adoption_complete` would go `False`, `allocations` would
be emptied, and the re-issue would never be allocated. `_adoption_complete`
therefore takes the suppressed slot set and excludes those slots from
`readable_coded_slots`: they are accounted for by the hold, not unaccounted.
For the same reason, `issuance.unaccounted_slots` already counts a hold owner's
slot as claimed, because the hold owner keeps its `lockname` and `slot`.

### 5. The cycle step

`CycleRequest` gains `forced_reissues: tuple[ForcedReissueDirective, ...]`
(defaulting to empty, so every existing construction keeps working), and
`issuance.resolve_cycle` gains two steps inside the **existing single lock
hold** — no new lock, no re-entrancy, and `_lock` is still never acquired twice:

```text
resolve_cycle(request):
    apply_forced_reissues(...)    # NEW: first, before adoption
    adopt ...                     # unchanged
    rekey ...                     # unchanged
    allocate ...                  # unchanged
    sweep ...                     # MOD: skips hold owners
    release_forced_holds(...)     # NEW: guarded, exempted, reports
```

`apply_forced_reissues` runs first so that adoption and allocation see a
registry in which the target owns nothing. For each directive it:

1. finds the record the target identity currently owns, or for a bare lock/slot
   target the owner whose `entry_id`, `lockname`, and `slot` match the target;
   if there is none, an identity-backed target records `no_existing_allocation`
   and continues to allocation, while a bare slot target is terminal because
   there is no reservation identity for a replacement allocation;
2. determines the replaced physical code. If an observed-alias owner for the
   same entry, lock, and slot exists on a different record, that observed code
   is the replaced code: the observed-alias owner is collapsed into the hold and
   the target identity is removed from its stale non-physical record. If both
   owners are on the same record, they are collapsed there. In either case no
   physical code is left unheld and the registry still enforces one code per
   identity;
3. renames that single retained owner's `identity_key` to the hold identity,
   preserving `entry_id`, `lockname`, `slot`, `origin`, `lock_observed`, and
   timestamps, and updates `by_identity` accordingly;
4. records the replaced code's `code_ref` in the directive outcome.

`release_forced_holds` walks this entry's hold owners, builds one
`ForcedReleaseExemption` **from each owner it is about to evaluate**, calls the
guard, and releases through `AllocationRegistry.release(hold_key)` when the
guard returns `None`. Retentions are reported with their reason (FR-021);
`adoption_conflict` can never appear among them, because it is exempted, which
is precisely why an exempted deferral is never reported as stuck.

`_sweep_unlocked` gains one early `continue` for hold-namespace owners. Without
it the sweep — which sees any non-active identity as sweepable — would evaluate
holds with the *full* guard and report a duplicate's hold as
`adoption_conflict`-retained, which FR-021 forbids. The skip is what keeps the
ordinary sweep semantics literally unchanged for everything else.

### 6. Allocation of the replacement (FR-011, FR-012, FR-008)

Nothing new. With retention suppressed, the reservation builder's
`generate_slot_code` output is the reservation's `slot_code`, the existing
`build_allocation_requests` passes it as `preferred_code`, and
`issuance.allocate_request` either takes the preferred value or walks the
existing deterministic `candidate_codes` sequence. That gives FR-012's
"generator-preferred when available, collision-resolved when not" for free.

The old code can never be re-issued as the replacement: its record still exists
with the hold owner, so `AllocationRegistry.is_available` returns `False` for it
(the record's single remaining owner is the hold identity, not the requesting
identity). Verified against `registry.is_available`.

For an identity-backed target, exhaustion returns
`AllocationResult(code=None, reason="exhausted")` as it does today, then the
forced-reissue step rolls back the staged hold in the same locked cycle: the
original owner is restored, the hold identity is removed, and the pending
record is cleared with `terminal_reason="code_space_exhausted"`. FR-008's
"leave the existing code in place" therefore holds in both the physical lock
and the registry. The service reports the failure and, in dry-run, refuses up
front.

For a bare lock/slot target with no reservation identity, no allocation request
is built and no replacement code is possible. The operation is clear-only: the
targeted slot is allowed to clear through the ordinary plan, and any registry
owner for that lock and slot is held and released under the same guard. A bare
slot with no matching registry owner records `no_existing_allocation` as a
terminal outcome and clears the pending state.

### 7. Dry run (FR-007, FR-023)

`DoorCodeAllocator.async_preview_reissue(request) -> ReissuePreview` takes the
lock, performs **no** mutation and **no** `_store.async_save`, and answers "what
would be issued" for identity-backed targets or "what would be cleared" for a
bare ghost slot. To avoid two divergent selection implementations, the
candidate-selection body of `issuance.allocate_request` is extracted into a pure
`select_code(registry, preferred_code, code_length, identity_key, *, exclude)`
helper that both the real path and the preview call. `exclude` carries the
target's current code so the preview models the post-hold registry without
building one.

The preferred code handed to the preview comes from the same reservation builder
the real cycle uses, run against the coordinator's cached calendar with
suppression applied. The preview runs that preparation against isolated copies
of the slot mappings and diagnostics, and it carries the same observation,
adoption-complete, pending-recovery, and unaccounted-slot guards that can block
the real cycle. If one of those guards would prevent issuance, the preview
reports that guard instead of returning a speculative code. The service never
calls a generator itself (FR-011).

The preview response is the **only** place a raw code appears (FR-023): it
returns `replacement_code` alongside `replacement_code_ref`. Every other
response, every log line, every notification, and every diagnostics payload
carries `code_ref` only (FR-022, FR-024). A test asserts field-by-field — not by
substring-searching the payload, which both false-positives on timestamps and
false-negatives on real leaks — that no non-dry-run response field equals a
known code.

### 8. Observability and audit (FR-021, FR-024, FR-025)

One `_LOGGER.info` line per accepted invocation, emitted by the service before
the refresh, recording: entry id, target form, identity key or lock and slot,
`call.context.user_id` and `call.context.origin` as the invoker, whether the
checked-in override was used, whether it was a dry run, and the replaced
`code_ref`. One further `info` line from the cycle records the replacement
`code_ref` and its origin, and one records the replaced code's disposition —
released, or retained with its reason. Those three lines make every forced
re-issue reconstructable from logs alone (SC-008) with no raw code anywhere.

A hold whose release has been deferred for more than one cycle is surfaced to
the operator through the existing persistent-notification mechanism the
allocator already uses, carrying the retention reason. An exempted
multiple-owner deferral cannot appear there by construction (FR-021).

Allocator diagnostics (`allocator/diagnostics.py`) gain a count of outstanding
forced-release holds and their retention reasons, as `code_ref` only.

`adoption.py` gets one narrow change: while a hold exists for an entry, lock,
and slot, the identity-mismatch report for that same slot is suppressed. After a
re-issue, the target identity owns the new code while the slot still reads the
old one, which is exactly the shape `_report_identity_mismatch` warns about —
correctly, in general, but here it is the expected intermediate state and would
fire a warning plus a persistent notification on every cycle until the write is
confirmed. The mismatch *detection* is unchanged; only the alarm is silenced
while the state is explained by a hold.

## Requirements traceability

| FR | Where it is discharged |
|----|------------------------|
| FR-001 | `rental_control.force_reissue`, `SupportsResponse.OPTIONAL` |
| FR-002 | Entity field or `lockname` + `slot`; decision 2 |
| FR-003 | Schema validation order in the contract; `ServiceValidationError` |
| FR-004 | Single entity id, no lists, no area/device, no wildcard |
| FR-005 | Managed range check against `start_slot` / `max_events` |
| FR-006 | Check-in guard via `CHECKIN_SENSOR` state; `force` opt-in |
| FR-007 | `async_preview_reissue`, no mutation, no store save |
| FR-008 | `select_code` exhaustion → refuse; rollback restores old owner |
| FR-009 | `PendingReissue` lifecycle + existing hold check; decision 3 |
| FR-010 | Refuse at invocation on `SlotStatus.UNKNOWN`; defer under guard after |
| FR-011 | Replacement comes only from `issuance`; service calls no generator |
| FR-012 | Builder's `generate_slot_code` value is the `preferred_code` |
| FR-013 | `PendingReissue`, in memory, one cycle, one target; decision 4 |
| FR-014 | `registry.allocate` records the new owner before any lock write |
| FR-015 | Existing `OVERWRITE_MANUAL_CHANGE` path; no code change needed |
| FR-016 | Lockless hold releases immediately; `get_slot_code` publishes at once |
| FR-017 | Pending targets are excluded before ghost hydration |
| FR-018 | `registry.release` returns the code to the pool; no retire flag |
| FR-019 | [The FR-019 guard exemption](#the-fr-019-guard-exemption) |
| FR-020 | Hold owner keeps the record alive; `is_available` stays `False` |
| FR-021 | `release_forced_holds` retries each cycle and reports reasons |
| FR-022 | Non-dry-run response carries `code_ref` only |
| FR-023 | `replacement_code` exists on the dry-run response only |
| FR-024 | `code_ref` in every log, notification, and diagnostics payload |
| FR-025 | Three-line audit trail; decision 8 |
| FR-026 | Already satisfied by `get_slot_code`; no attribute change |
| FR-027 | No config flow or options change of any kind |
| FR-028 | No automatic invocation path exists; service calls only |

## Test strategy

Commands: `uv run pytest tests/ -q -p no:randomly` and
`uv run ruff check custom_components/ tests/`. Coverage floor is 95%, so new
modules need tests in the same commit.

Known flakes to expect and **not** to chase:
`tests/integration/test_refresh_cycle.py::test_missing_store_adopts_coded_slots_when_unavailable_at_setup`
and `::test_deleted_store_reenable_recovers_coded_slots`. They share a fixture
and fail together spuriously. Re-run them in isolation before treating either as
a regression.

### Mandatory: the ordinary release paths are unaffected

`tests/unit/test_allocator_release_guard.py` is a regression suite whose only
job is to prove the exemption changed nothing outside its scope. It must assert:

1. **Parity across all three ordinary callers.** For a matrix of registry and
   observation states — single owner / two owners; lock covered / not covered;
   code observed / not observed / slot unreadable (`SlotStatus.UNKNOWN`) /
   slot `FREE`; lockless owner — `_sweep_unlocked`,
   `async_mark_entry_removed`, and `async_clear_orphans` produce exactly the
   same released and retained sets, with the same reasons, as they do on the
   pre-feature code. A two-owner record is still retained as
   `adoption_conflict` by all three.
2. **No ordinary caller can pass an exemption.** A static assertion over the
   production tree that `ForcedReleaseExemption(` appears in exactly one
   non-test module, `allocator/reissue.py`, and that `forced_release=` appears
   at exactly one call site.
3. **Default is no exemption.** Calling `_release_guard_reason` without the
   keyword on a two-owner record returns `adoption_conflict`.
4. **The exemption is inert when it does not match.** Wrong `code`, wrong
   `entry_id`, wrong `identity_key`, a non-hold identity key, or an `owners`
   list of length two each fall back to the full guard and still return
   `adoption_conflict`.
5. **Physical conditions survive the exemption.** With a correctly matching
   exemption on a two-owner record: an uncovered lock still returns
   `unverifiable_lock`; an observed code still returns `code_still_programmed`;
   an unreadable (`SlotStatus.UNKNOWN`) target slot still returns
   `code_still_programmed`; and a `SlotStatus.FREE` target slot does **not**.
6. **Scope is one owner.** With the exemption applied to a two-owner record,
   only the named hold owner is released; the other owner is untouched, the
   record survives with one owner, and the code remains unavailable to a third
   identity.

### Mandatory: end-to-end duplicate healing

`tests/integration/test_reissue_duplicate_healing.py` builds the live production
condition and drives it to completion:

- two config entries with carved-out, disjoint slot ranges on **one shared
  parent lock**, one reservation each, both slots physically programmed with
  the **same** code;
- one refresh so both sides adopt, producing one registry record with two owners
  and a reported adoption conflict;
- one `force_reissue` invocation against **one** side's reservation sensor;
- assert immediately after that cycle: the targeted reservation now holds a
  different code; the untargeted reservation's code is unchanged; the old
  record still has two owners (the hold plus the untouched side), so the old
  code is still unavailable; the plan emitted `OVERWRITE_MANUAL_CHANGE` for the
  targeted slot only;
- advance the simulated lock so the targeted slot reads the new code, run one
  more refresh, and assert the **end state**: two distinct codes on the lock;
  the old record reduced from two owners to exactly one — the untargeted entry;
  no hold remaining; and no duplicate reported for that code any longer.

### Other required coverage

Unit:

- `reissue.py` hold identity: round-trips, is recognised by
  `is_forced_release_hold`, and can never collide with a reservation fingerprint
  or with an `observed_alias_key`.
- Directive application: identity freed, owner re-homed with `lockname`, `slot`,
  and `lock_observed` preserved; observed alias for the same slot collapsed even
  when it lives on a different record; an identity target with no existing
  allocation yields `no_existing_allocation` and still allocates; a bare slot
  target with no owner is terminal and does not allocate.
- Suppression: `_resolve_observed_code` returns the generated code for the
  suppressed target and `manual_observed` for every other reservation in the
  same cycle; the suppression is gone on the next cycle; a restart drops it.
- `_adoption_complete` stays `True` when the only unadopted readable coded slot
  is the suppressed target — the regression that would otherwise silently
  disable issuance for the whole entry for that cycle.
- `build_protected_reservation` honours suppression.
- Service: both-targets and neither-target refusals; a list of entity ids
  refused; a non-Rental-Control entity refused; a slot outside the managed range
  refused; an ambiguous slot refused; an unreadable target slot refused; a
  checked-in target refused without `force` and accepted with it; a repeat call
  producing exactly one rotation; dry run mutating nothing in the registry, on
  any lock, or in any sensor state while still returning a raw
  `replacement_code`; every non-dry-run response field asserted not to equal any
  known code.
- Exhaustion: full code space, invocation fails, the existing code is still on
  the lock and still owned.

Integration:

- Deferred release: the write does not confirm for two cycles; the hold is
  retried and reported each cycle with `code_still_programmed`; the old code
  cannot be allocated to a third reservation meanwhile; release happens on the
  cycle after confirmation.
- Lockless entry: the new code publishes immediately and the old code is
  released in the same cycle.
- A reservation that disappears from the feed between invocation and the cycle:
  no slot is created or resurrected, and the pending record is dropped.
- Ghost slot with no reservation (US4): slot-targeted invocation clears the bad
  code from the slot and handles its record under the same guard.

Existing suites to update rather than delete: any test constructing
`ReservationBuildContext`, `CycleRequest`, or `_release_guard_reason` by keyword
should keep passing on the new defaults, which is itself the compatibility
assertion.

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| The exemption is widened later into a general "force release" flag, releasing a live code | It is a typed value object, not a boolean; it requires a hold-namespace identity; the regression suite asserts exactly one construction site and one call site |
| A hold outlives its lock forever and quietly pins a code | Every deferral is retried and reported each cycle with its reason (FR-021); `clear_orphaned_codes` remains the operator's escape hatch |
| Home Assistant restarts between invocation and the cycle | Documented and accepted: the suppression lapses and the operator re-invokes (FR-013). The registry hold, if one was already created, is durable and keeps being retried, so no code is lost or double-issued |
| An entry is removed while it holds a forced-release hold | `async_mark_entry_removed` and `async_clear_orphans` deliberately keep the **full** guard, so such a hold is retained rather than released. It surfaces as an orphan with a reason, and the operator clears it once the code is provably gone. This is the safe direction |
| The identity-mismatch notification fires every cycle during the intermediate state | Suppressed only while a hold explains that exact entry, lock, and slot; the detection itself is unchanged |
| Slot targeting picks the wrong config entry on a shared parent lock | Ranges are disjoint by construction; the resolver refuses on ambiguity instead of guessing (FR-005) |
| A second invocation chains a second rotation | The `PendingReissue` lifecycle, registry hold check, and same-runtime completed fingerprint make a repeat a no-op (FR-009, SC-007) |
| Suppressing an adoption silently disables issuance for the whole entry | `_adoption_complete` excludes suppressed slots; a dedicated unit test locks that in |

## Phase 0 Research Output

See [research.md](research.md). All planning questions are resolved; no
`[NEEDS CLARIFICATION]` markers remain.

## Phase 1 Design Output

See [data-model.md](data-model.md) for entities, states, and validation rules,
[contracts/force-reissue-service.md](contracts/force-reissue-service.md) for the
service contract and the allocator API delta, and [quickstart.md](quickstart.md)
for the implementation and validation guide. Agent-context updates are omitted
because no new language, framework, dependency, or tool is introduced.

## Post-Design Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I: Code Quality & Testing | PASS | The exemption predicate, hold identity helpers, and directive application are pure functions; HA coupling stays in the service module and one coordinator helper. |
| II: Atomic Commit Discipline | PASS | Six natural implementation commits identified above. |
| III: Licensing & Attribution | PASS | SPDX headers on every new file. |
| IV: Pre-Commit Integrity | PASS | No bypass; quickstart gates on the full hook run. |
| V: Agent Co-Authorship & DCO | PASS | Sign-off plus co-author trailer. |
| VI: User Experience Consistency | PASS | No attribute, entity, or configuration change; one new service declared in all four surfaces. |
| VII: Performance Requirements | PASS | No new store, no new lock, one bounded extra registry walk per cycle. |

**Gate result: PASS** — no plan-stage constitution violations.

## Complexity Tracking

No constitutional violations require justification.

Two design points are recorded rather than hidden.

**The forced-release hold is persisted ownership.** FR-013 requires the
*suppression* to be in memory only, and it is. But FR-020 requires the replaced
code to stay unavailable to every other allocation while its release is
deferred, and the only mechanism in this system that makes a code unavailable is
registry ownership, which is persisted. The hold is therefore a persisted
**allocation** fact, not persisted **suppression** state, and it adds no field,
no schema change, and no store-version bump: it is an ordinary
`AllocationOwner` whose `identity_key` uses a reserved namespace, precisely as
`observed_alias_key` owners already do. The alternative — an in-memory
reservation of the code — was rejected because it would evaporate on restart and
allow a still-programmed code to be re-issued, which is the exact defect this
feature exists to fix.

**A restart drops the suppression but not the hold.** That asymmetry is
intentional and safe. A dropped suppression means the re-issue simply does not
happen and the operator invokes again. A surviving hold means a replaced code
stays unavailable and keeps being retried until it is provably gone. Both
failure directions are conservative.

## Phase Notes

- PLAN stage stops here. Do not create `tasks.md` and do not modify production
  code in this PR.
- Live source on `main` is the truth. Every symbol, signature, and line number
  cited here was read from the tree at `a9b82f9`. Where a future reading and
  this plan disagree, re-read the code and adjust the plan.
- Settled spec decisions are not reopened. No bulk mode, no code retirement, no
  automatic healing, no new configuration, no operator-supplied replacement
  code, and no new sensor attribute.
