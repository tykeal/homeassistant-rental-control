<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Implementation Plan: Slot-Exclusion Recovery Control

**Feature**: `024-slot-exclusion` | **Planning Branch**:
`024-slot-exclusion-plan` | **Date**: 2026-10-15 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/024-slot-exclusion/spec.md` and
GitHub issue #802, which reports that one permanently unavailable slot freezes
door-code issuance entry-wide.

## Summary

Add one operator-invoked Home Assistant service,
`rental_control.set_slot_exclusion`, that marks exactly one permanently
unreadable managed slot on exactly one config entry as excluded from the
allocator's unreadable-slot accounting — and nothing else.

The feature is deliberately small in behaviour and precise in blast radius. It
does not change the fail-closed gate's logic, does not scope the shared
pending-adoption set per entry, and does not touch `reconciliation/` at all.
It changes what three specific gate checks are allowed to count, and leaves the
same observation untouched for every other reader.

Four things carry the feature, each verified against live source on `main` at
`edb6a7f` rather than taken from prose (see [research.md](research.md), which
also records the specification citations that had drifted):

1. **A gate-only subtraction, carried beside the observation.** The excluded
   set travels as an additional `frozenset[int]` field on
   `CodeResolutionRequest` and `CycleRequest` and as a keyword-only parameter
   on the two gate helpers. `build_cycle_observation` keeps producing the raw
   `unreadable_slots` set. This is the load-bearing design decision of the
   feature and the one most likely to be implemented wrongly; see
   [Why the filter is not in the observation](#why-the-filter-is-not-in-the-observation).

2. **Three gate members, not one.** `unaccounted_slots`
   (`allocator/issuance.py:239-253`), the unreadability branch of
   `_adoption_complete` (`coordinator_helpers/code_allocation.py:249-250`), and
   the re-issue preview's own independent raw rejection
   (`allocator/reissue_preview.py:111-112`). The third is a separate check that
   runs *before* the preview consults `unaccounted_slots` at line 113, so
   fixing the first two leaves `force_reissue` blocked and FR-019 unmet.

3. **Persistence in config-entry `data`, re-validated every cycle.** A new
   `CONF_EXCLUDED_SLOTS` key written through
   `hass.config_entries.async_update_entry`, because `update_listener`
   (`__init__.py:256-281`) rebuilds `data` from `options.copy()` and then
   clears `options`. Every cycle re-derives the set from the live observation
   and drops any exclusion whose slot is readable again or no longer managed,
   which is what keeps the persisted set non-authoritative (FR-031).

4. **Three refusal guards with no override.** Out-of-range, readable, and
   claimed slots cannot be excluded, and there is no `force` flag — unlike
   `force_reissue`, which has one. The guards apply to *setting* an exclusion
   only; clearing one is always permitted (FR-012).

One requirement needs **no code at all**, and establishing that was worth more
than any other hour spent on this plan:

- **FR-017 and SC-004 are already satisfied.** `_classify_slot`
  (`reconciliation/desired.py:258-259`) already maps `SlotStatus.UNKNOWN` to
  `ActionKind.BLOCKED` with reason `"unreadable"`, so an excluded slot is
  already never written to, never cleared, and never offered as a free slot.
  That remains true only because exclusion is kept out of the planner's view
  entirely. No file under `custom_components/rental_control/reconciliation/`
  is modified by this feature, and a test asserts it.

## Technical Context

**Language/Version**: Python >=3.14.2
**Primary Dependencies**: Home Assistant runtime >=2026.4.0 per `hacs.json`;
`homeassistant.core.ServiceCall` / `SupportsResponse`,
`homeassistant.config_entries.ConfigEntry` /
`ConfigEntries.async_update_entry`,
`homeassistant.helpers.config_validation`,
`homeassistant.components.persistent_notification`; dev/test dependency
`homeassistant>=2026.6.0`; test tooling
`pytest-homeassistant-custom-component`
**Storage**: **No new store, no new store schema, and no schema version bump.**
One new key, `excluded_slots`, in the existing config entry's `data` mapping.
The feature-022 registry at `rental_control.code_registry` (version 1) and the
reconciliation `Store` are both untouched.
**Testing**: `uv run pytest tests/ -q -p no:randomly` and
`uv run ruff check custom_components/ tests/`; pre-commit for ruff-format,
mypy, interrogate, reuse, yamllint, markdownlint, gitlint
**Target Platform**: Home Assistant custom integration on the HA asyncio event
loop
**Project Type**: Single Home Assistant custom integration
**Performance Goals**: The service performs O(1) config-entry work and no
allocator-lock work at all — it never calls the allocator. The per-cycle
re-validation is one set difference over the excluded set, which is bounded by
`max_events` and is typically empty; when it is empty, the entire feature costs
two frozenset intersections per cycle and performs zero writes. A persistence
write happens only on an actual change, never on an unchanged cycle.
**Constraints**: No new operator configuration in the config or options flow
(FR-034). No force flag or override on any of the three guards (FR-009). No
bulk, wildcard, or range mode (FR-004). No raw door code in any response,
notification, diagnostics payload, or log line (FR-028, SC-009). No change to
calendar sensor attributes. No change under `reconciliation/`. 95% coverage
floor. Files below 400 lines, functions below 80 lines, at most six parameters,
no aislop suppression.
**Scale/Scope**: The motivating deployment is several config entries against
shared parent locks with carved-out slot ranges; the pathological case is one
dead slot on one entry freezing every entry in the system at every restart.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I: Code Quality & Testing | PASS | The exclusion set algebra, the persisted-shape parser, and the three refusal guards are pure and unit-testable without Home Assistant. The HA-facing surface is one domain service handler plus one config-entry write. The test strategy names the mandatory FR-018/SC-014 release-evidence regression and the end-to-end US1→US3 scenario. |
| II: Atomic Commit Discipline | PASS | This PR is docs-only and splits into one PLAN commit and one TASKS commit, matching features 022 and 023. Implementation splits into: constants and pure helpers; the three gate members; cycle wiring and persistence; re-validation and auto-clear; the service surface; notifications and diagnostics. |
| III: Licensing & Attribution | PASS | Every new markdown file carries the SPDX header pair; `specs/**` is additionally covered by the `REUSE.toml` aggregate annotation. Future Python modules carry the `#`-comment form. |
| IV: Pre-Commit Integrity | PASS | No hook bypass. `quickstart.md` defines the validation gate. |
| V: Agent Co-Authorship & DCO | PASS | Both commits use `git commit -s` plus an AI co-author trailer. |
| VI: User Experience Consistency | PASS | No config flow field, no options flow field, no entity rename, no attribute change. One new service declared in `services.yaml`, `strings.json`, and both shipped translations, exactly like `force_reissue` and `clear_orphaned_codes` (FR-035). |
| VII: Performance Requirements | PASS | No new I/O in the sensor path, no new store, no additional registry walk. One bounded set difference per cycle and a config-entry write only when the set actually changes. |

**Gate result: PASS** — no violations. Proceeding to Phase 0.

## Project Structure

### Documentation (this feature)

```text
specs/024-slot-exclusion/
├── plan.md                              # This file
├── research.md                          # Phase 0 decisions, alternatives,
│                                        #   and corrected source citations
├── data-model.md                        # Phase 1 entities, states, validation
├── quickstart.md                        # Phase 1 implementation/validation
├── contracts/
│   └── set-slot-exclusion-service.md    # Service contract + gate API delta
├── checklists/requirements.md           # Existing spec-stage checklist
└── tasks.md                             # Phase 2 output
```

`contracts/` is present for the same reason it was in features 022 and 023:
this feature introduces a Home Assistant service with a service response, plus
an internal API delta on helpers with more than one caller. It introduces **no**
persisted store change, so the contract records explicitly that the registry
and reconciliation store shapes are untouched.

### Source Code (repository root)

```text
custom_components/rental_control/
├── allocator/
│   ├── exclusions.py              # NEW: pure set algebra, persisted-shape
│   │                              #      parse/serialise, refusal evaluation
│   ├── exclusion_service.py       # NEW: set_slot_exclusion registration,
│   │                              #      handler, response building
│   ├── issuance.py                # MOD: unaccounted_slots gains keyword-only
│   │                              #      `excluded`; resolve_cycle passes
│   │                              #      request.excluded_slots to it and to
│   │                              #      _track_pending_adoption_slots
│   ├── models.py                  # MOD: CycleRequest.excluded_slots
│   ├── reissue_preview.py         # MOD: gate member 3 — narrow the raw
│   │                              #      unreadable rejection and pass the
│   │                              #      excluded set to unaccounted_slots
│   ├── diagnostics.py             # MOD: per-entry excluded_slots projection
│   └── services.py                # MOD: register the new service from
│                                  #      register_allocator_services
├── coordinator_helpers/
│   ├── slot_exclusions.py         # NEW: load from entry data, per-cycle
│   │                              #      re-validation, persistence write,
│   │                              #      notification reconcile/dismiss
│   ├── code_allocation.py         # MOD: _adoption_complete gains `excluded`;
│   │                              #      async_resolve_codes re-validates,
│   │                              #      threads the set, and reports the
│   │                              #      gated set to report_unreadable_slots
│   ├── reissue.py                 # MOD: CodeResolutionRequest.excluded_slots
│   ├── reissue_targets.py         # MOD: export entry_id_for_entity (pure
│   │                              #      rename of _entry_from_sensor_state)
│   └── exclusion_targets.py       # NEW: entry/slot resolution for exclusion,
│                                  #      which cannot reuse resolve_slot_target
├── const.py                       # MOD: CONF_EXCLUDED_SLOTS
├── __init__.py                    # MOD: dismiss the exclusion notification
│                                  #      alongside the unreadable one
├── services.yaml                  # MOD: set_slot_exclusion declaration
├── strings.json                   # MOD: name/description/fields
└── translations/{en,fr}.json      # MOD: service translations

tests/
├── unit/
│   ├── test_slot_exclusions.py             # parser, serialiser, set algebra,
│   │                                       #   re-validation, FR-032 tolerance
│   ├── test_exclusion_service.py           # schema, targeting, three guards,
│   │                                       #   dry run, response shape
│   ├── test_exclusion_targets.py           # entry/slot resolution + refusals
│   ├── test_exclusion_gate.py              # the three gate members
│   └── test_exclusion_release_evidence.py  # FR-018/SC-014 mandatory regression
└── integration/
    ├── test_exclusion_recovery.py          # US1 + US3 end-to-end
    ├── test_exclusion_multi_entry.py       # US2 across three entries
    └── test_exclusion_notifications.py     # US4 reconcile and dismiss
```

**Structure Decision**: The split follows feature 022's boundary and feature
023's module naming. `allocator/exclusions.py` is **pure**: it takes raw
persisted values, observations, and slot numbers, and imports no Home
Assistant. Everything that needs `hass` — the service handler, the config-entry
write, the notification, and the target resolution — lives in
`allocator/exclusion_service.py`, `coordinator_helpers/slot_exclusions.py`, and
`coordinator_helpers/exclusion_targets.py`. No new mixin is added to the
coordinator and no new coordinator attribute is introduced; per-cycle state is
derived from the observation and the config entry, never cached on the
coordinator, because a cached copy is a divergence waiting to happen (FR-024).

## Why the filter is not in the observation

This is the design element most likely to be got wrong, so it is specified
concretely rather than by intent. The obvious implementation — subtract the
excluded slots inside `build_cycle_observation`, where `unreadable_slots` is
computed in one place — is **unsafe**, and FR-016 exists to forbid it.

### What else reads `unreadable_slots` (verified, not assumed)

`custom_components/rental_control/allocator/allocator.py:378-408`:

```python
    def _owner_still_programmed(
        self,
        record: AllocationRecord,
        owner: AllocationOwner,
        observations: list[CycleObservation],
        *,
        refresh_observed: bool = True,
    ) -> bool:
        ...
            programmed = (
                record.code in observation.observed_codes
                or owner.slot in observation.unreadable_slots
            )
```

Here `unreadable_slots` is **evidence that a code may still be physically
programmed**, and it is the reason `_release_guard_reason`
(`allocator/allocator.py:346-376`) returns `code_still_programmed` and refuses
to return that code to the pool. The same observations are rebuilt by
`services._collect_observations` (`allocator/services.py:98-112`) and fed to
`async_clear_orphans` for orphan cleanup.

### The consequence of filtering at the source

If `build_cycle_observation` subtracted the excluded slots, then for an
excluded slot:

1. `owner.slot in observation.unreadable_slots` becomes `False`;
2. `record.code in observation.observed_codes` is already `False`, because
   `build_cycle_observation` only records codes from slots that are **not**
   `SlotStatus.UNKNOWN` (`code_allocation.py:270-272`) — an unreadable slot
   contributes no code by construction;
3. so `_owner_still_programmed` returns `False`, `_release_guard_reason`
   returns `None`, and the code is released back into the shared pool.

That is a code released **on the strength of an exclusion**, which FR-018
prohibits and SC-014 measures at zero. It is strictly worse than the freeze:
the freeze withholds a code, this hands a still-live credential's value to the
next guest. The orphan path makes it worse again, because an orphaned owner
belongs to a removed entry that the FR-010 claim-check cannot see, so no guard
anywhere else would catch it.

### What is done instead

The excluded set is carried **beside** the observation, as a `frozenset[int]`,
and is subtracted only at the three gate members. Every other reader receives
the raw observation and is byte-for-byte unaffected. Concretely:

| Consumer | Receives | Why |
|----------|----------|-----|
| `unaccounted_slots` (G1) | raw observation + `excluded` | Gate member. |
| `_adoption_complete` (G2) | raw observation + `excluded` | Gate member. |
| `reissue_preview` raw check (G3) | raw observation + `excluded` | Gate member (FR-019). |
| `_track_pending_adoption_slots` | gated set | Diagnostic only; an excluded slot no longer blocks adoption, so reporting it as blocking would be false. |
| `report_unreadable_slots` | gated set | Its message says issuance is withheld; for an excluded slot that is untrue. |
| `CycleRequest.observation` → `CycleResult` | raw observation | Release evidence. |
| `_owner_still_programmed` / `_release_guard_reason` | raw observation | **Release evidence. Never filtered.** |
| `services._collect_observations` → `async_clear_orphans` | raw observation | **Release evidence. Never filtered.** |
| `reconciliation/` | raw managed slots, no exclusion at all | FR-017; the planner's `BLOCKED, "unreadable"` treatment is what keeps an excluded slot unwritable. |

A test asserts the negative directly: it greps the production tree and fails if
`build_cycle_observation` ever references the excluded set.

## Design decisions

### 1. The service shape

`rental_control.set_slot_exclusion`, a **domain** service registered from
`register_allocator_services` (`allocator/services.py:39-57`), beside
`reissue_service.register_force_reissue_service(hass)` at line 42, with
`SupportsResponse.OPTIONAL` (FR-001).

```python
SET_SLOT_EXCLUSION_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTITY_ID): cv.entity_id,
        vol.Optional(ATTR_LOCKNAME): cv.string,
        vol.Required(ATTR_SLOT): vol.All(_positive_slot, vol.Range(min=1)),
        vol.Optional(ATTR_EXCLUDED, default=True): cv.boolean,
        vol.Optional(ATTR_REASON): cv.string,
        vol.Optional(ATTR_DRY_RUN, default=False): cv.boolean,
    }
)
```

Three differences from `force_reissue`'s schema, each required by the spec:

- `slot` is **required**, not optional. FR-002 states the slot is never
  inferred from the entity, because the entity identifies the *entry* while the
  slot being excluded is by definition one the integration cannot read and so
  has no sensor of its own.
- There is **no `force` field**. FR-009 and the Out of Scope section forbid any
  override of the three guards. This is the single most important difference
  between this service and `force_reissue`, and a test asserts the schema has
  no such key.
- `excluded` defaults to `True`, so the same service sets and clears (FR-005),
  and `reason` is accepted and recorded but affects no decision (FR-006).

### 2. Target resolution

Handled by a new `coordinator_helpers/exclusion_targets.py` rather than by
reusing `reissue_targets.resolve_slot_target`
(`coordinator_helpers/reissue_targets.py:71-111`), which is unsuitable on three
counts:

- it refuses with `slot_not_managed` before this service can emit its own
  FR-011 `slot_out_of_range` refusal;
- it resolves an identity key and a checked-in flag, neither of which an
  exclusion has any use for — an excluded slot is unreadable and therefore
  typically has no identity at all;
- FR-012 requires the range check to be **skipped entirely** when clearing,
  which `resolve_slot_target` cannot express.

The entity path does reuse existing code: `_entry_from_sensor_state`
(`reissue_targets.py:127-144`) is renamed to a public
`entry_id_for_entity_attributes` and exported, with no behaviour change, and
both features call it. Copying the sensor-attribute contract into a second
module is rejected: the captive-portal contract over `slot_name`, `start`, and
`end` must have exactly one reader.

Validation order, which is also the order refusal reasons are emitted:

1. `ambiguous_target` — both `entity_id` and `lockname` supplied (FR-003);
2. `missing_target` — neither supplied (FR-003);
3. `unknown_lock` / `ambiguous_lock` / `unknown_entity` — resolution failed;
4. `slot_out_of_range` — set only, skipped when clearing (FR-011, FR-012);
5. `slot_readable` — set only (FR-009, FR-012);
6. `slot_claimed` — set only (FR-010, FR-012).

Ordering 4 before 5 is deliberate: an out-of-range slot has no meaningful
observed status, so asking about its readability first would produce a
misleading refusal.

### 3. The three refusal guards

All three are evaluated in `allocator/exclusions.py` as pure functions over
values the caller supplies, so each is unit-testable without Home Assistant:

```python
def refusal_for_exclusion(
    slot: int,
    *,
    start_slot: int,
    max_events: int,
    unreadable_slots: frozenset[int],
    managed_slots: frozenset[int],
    claimed_slots: frozenset[int],
) -> str | None:
    """Return the refusal reason for excluding one slot, or None."""
```

- **Range** (FR-011): `start_slot <= slot < start_slot + max_events`, read from
  the resolved coordinator's `start_slot` and `max_events`, which is the same
  range `resolve_slot_target` computes at `reissue_targets.py:84-86`.
- **Readability** (FR-009): the slot must be in `unreadable_slots` of a
  freshly built observation — which is exactly `SlotStatus.UNKNOWN` over
  managed slots (`code_allocation.py:278-280`). A slot absent from
  `managed_slots` entirely is treated as out of range, not as readable.
- **Claim** (FR-010): the slot must not appear as `owner.slot` for any registry
  owner of the addressed entry on the addressed lock — the same predicate
  `unaccounted_slots` computes at `issuance.py:245-252`. That subtraction is
  extracted to a shared `claimed_slots(allocator, entry_id, lockname)` helper
  so the service and the gate cannot drift apart about what "claimed" means.

Clearing bypasses all three (FR-012), including for a slot that has since left
the managed range — otherwise a narrowed range could strand an exclusion that
the operator is explicitly trying to remove.

### 4. Persistence and the `data` write

`const.py` gains `CONF_EXCLUDED_SLOTS = "excluded_slots"`. The persisted value
is a JSON list of objects, parsed to `dict[int, str | None]`:

```json
"excluded_slots": [{"slot": 7, "reason": "lock replaced 2026-10-12"}]
```

The write goes through one helper in
`coordinator_helpers/slot_exclusions.py`:

```python
def persist_excluded_slots(
    hass: Any, entry_id: str, exclusions: dict[int, str | None]
) -> bool:
    """Write the entry's excluded slots; return whether anything changed."""
```

It is a no-op returning `False` when the serialised value equals what is
already stored, so an unchanged cycle performs zero writes.

**The reload hazard, and why it is not one.** `async_update_entry` fires the
entry's update listeners, and this integration registers `update_listener`
(`__init__.py:256`). Its first statement is:

```python
    if not config_entry.options:
        return
```

Outside an options-flow save `options` is empty, so the listener returns
immediately and cannot trigger a config rebuild or a reload loop. This is three
lines away from the thing it protects, so a regression test pins it rather than
leaving it to inspection.

**The options-flow interaction** is unchanged and intended: an options-flow
save rebuilds `data` from `options`, dropping every exclusion and returning the
entry to fail-closed. The spec documents this in Edge Cases; the plan adds only
that the service's log line and the notification both name the entry, so an
operator who re-excludes after a reconfiguration knows why.

### 5. Per-cycle re-validation and auto-clear

In `async_resolve_codes` (`coordinator_helpers/code_allocation.py:46-146`),
immediately after `build_cycle_observation` at line 68:

```python
    observation = build_cycle_observation(entry_id, lockname, managed_slots)
    excluded, cleared = slot_exclusions.revalidate(
        slot_exclusions.load(hass, entry_id), observation
    )
```

`revalidate` drops an exclusion when either:

1. the slot is **not** in `observation.unreadable_slots` — readable again, so
   FR-020 auto-clear applies; or
2. the slot is **not** in `observation.managed_slots` — the managed range
   moved, so FR-023 applies.

Both conditions are read off the same observation the gate consumes, which
makes the FR-024 "can never diverge" requirement structural. When `cleared` is
non-empty the new set is persisted, each cleared slot is logged at INFO naming
entry, lock, slot and cause (FR-022, FR-027), and the notification is
reconciled with the smaller set.

`revalidate` itself is pure — it takes a mapping and an observation and returns
`(kept, cleared)`. The impure parts (load, persist, log, notify) sit around it
in `slot_exclusions.py`.

The preview path does **not** re-validate or persist (FR-007, SC-007). It loads
the set, applies it to the gate, and returns. The ordinary cycle performs the
auto-clear moments later.

### 6. Threading the set to the gate

```text
slot_exclusions.load/revalidate
        │
        ├─► _adoption_complete(observation, adoptions, managed_slots,
        │                      suppression, excluded=excluded)        [G2]
        │
        └─► CodeResolutionRequest.excluded_slots
                 └─► CycleRequest.excluded_slots
                          └─► issuance.resolve_cycle
                                   ├─► unaccounted_slots(..., excluded=…)  [G1]
                                   └─► _track_pending_adoption_slots(
                                           …, unreadable - excluded)
```

and, independently, for the preview:

```text
slot_exclusions.load
        └─► reissue_preview.preview_request_and_result
                 ├─► if observation.unreadable_slots - excluded:  [G3]
                 └─► issuance.unaccounted_slots(…, excluded=…)    [G1]
```

Both new dataclass fields default to `frozenset()`, which keeps every existing
construction site compiling unchanged and makes the fail-closed value the
default. `CycleRequest.observation` continues to carry the **raw** observation
into `CycleResult` and into the release-evidence paths.

### 7. Operator-visible surfaces

**Notification (FR-025, FR-030).** A new per-entry notification in
`coordinator_helpers/slot_exclusions.py`, with id
`f"{DOMAIN}_excluded_slots_{entry_id}"`, reconciled on every cycle and
dismissed when the entry's excluded set becomes empty — the
reconcile-and-dismiss pattern of `report_forced_hold_deferrals`
(`allocator/services.py:164-226`) and of `report_unreadable_slots`
(`coordinator_helpers/code_allocation.py:160-195`). Its message names the entry,
the lock, the excluded slots with their recorded reasons, and states the FR-030
residual risk in plain terms: the slot may still hold a code the integration
cannot see, and a newly issued code could collide with it. It is also dismissed
on unload and removal in `__init__.py`, beside the existing
`dismiss_unreadable_slots` calls at lines 203 and 214.

**The `6a6602b` notification is narrowed, not duplicated.** The existing
unreadable-slot notification tells the operator that "Door code issuance is
withheld while those slots stay unreadable". For an excluded slot that is
false. So the call at `code_allocation.py:120-122` passes
`sorted(observation.unreadable_slots - excluded)` and dismisses itself when the
last non-excluded unreadable slot is excluded or recovers. The two
notifications then have disjoint subjects by construction — a slot is in
exactly one of the two sets — and answer two different operator questions:
"why has issuance stopped?" and "what risk am I carrying?".

**Diagnostics (FR-026).** `allocator_diagnostics`
(`allocator/diagnostics.py:18-58`) gains an `excluded_slots` key mapping
`entry_id` to the sorted slot numbers and their reasons, derived by walking
`allocator.hass.config_entries.async_entries(DOMAIN)` and parsing each entry's
`CONF_EXCLUDED_SLOTS` through the same tolerant parser. No code is included,
and the coordinator-side `scrub_codes`
(`coordinator_helpers/diagnostics.py:21-32`) continues to apply.

**Logging (FR-027).** Setting, clearing, and auto-clearing each log once at
INFO naming entry, lock, slot and cause. Refusals log at DEBUG with the reason.
No log line contains a code (FR-028).

### 8. What is deliberately not built

- No repairs flow. Deferred by the spec; the service is written so one can
  later wrap it without semantic change.
- No config or options flow field (FR-034).
- No bulk or wildcard mode (FR-004) — the schema takes one `slot`, an `int`.
- No `force` flag (FR-009).
- No sensor attribute advertising exclusion; the captive-portal contract over
  `slot_code`, `slot_name`, and `last_four` is untouched.
- No change under `reconciliation/`.

## Requirements traceability

| Requirement | Where it is satisfied |
|-------------|------------------------|
| FR-001, FR-035 | `allocator/exclusion_service.py`; `services.yaml`; `strings.json`; `translations/{en,fr}.json` |
| FR-002, FR-003 | `coordinator_helpers/exclusion_targets.py`; schema `slot` required |
| FR-004 | Schema accepts one `int` slot; no list, range, or wildcard type |
| FR-005, FR-006 | `excluded` default `True`; `reason` recorded, never read by a decision |
| FR-007, SC-007 | Dry-run branch returns before any write, notification, or re-validation |
| FR-008 | Response contract in [contracts/set-slot-exclusion-service.md](contracts/set-slot-exclusion-service.md) |
| FR-009, FR-010, FR-011 | `exclusions.refusal_for_exclusion`; no `force` key in the schema |
| FR-012 | Clear path skips all three guards |
| FR-013 | Refusal returns before persist, notify, and log-at-INFO |
| FR-014 | G1: `unaccounted_slots(..., excluded=…)` |
| FR-015 | G2: only the `unreadable_slots` branch of `_adoption_complete` is narrowed; the readable-coded-slot condition below it is untouched |
| FR-016, SC-006, SC-014 | Gate-only threading; [Why the filter is not in the observation](#why-the-filter-is-not-in-the-observation); `test_exclusion_release_evidence.py` |
| FR-017, SC-004 | Already satisfied by `reconciliation/desired.py:258-259`; asserted, not modified |
| FR-018 | Raw observation reaches `_owner_still_programmed` and orphan cleanup unchanged |
| FR-019, SC-011 | G3: `reissue_preview.py:111-112` narrowed, plus `excluded` passed at line 113 |
| FR-020, FR-021, SC-003 | `slot_exclusions.revalidate`, called unconditionally every cycle |
| FR-022, FR-027 | INFO log plus notification reconcile on every auto-clear |
| FR-023, FR-032, SC-010 | `revalidate` drops unmanaged slots; the parser never raises |
| FR-024 | Re-validation and the gate consume one observation object |
| FR-025, SC-008 | Per-entry notification reconciled and dismissed every cycle |
| FR-026 | `allocator/diagnostics.py` `excluded_slots` projection |
| FR-028, SC-009 | No code-bearing value in any new surface; `scrub_codes` unchanged |
| FR-029, SC-002 | Config-entry `data` persistence |
| FR-030 | Notification text and `strings.json` description |
| FR-031 | `revalidate` runs before any consumption; the set only ever subtracts from the gate |
| FR-033 | Exclusion is written only by the service handler |
| FR-034 | No config or options flow change |

## Test strategy

### Mandatory: release evidence is unaffected

The feature's worst failure mode is releasing a code because a slot was
excluded. `tests/unit/test_exclusion_release_evidence.py` must prove it cannot
happen:

1. An entry with an excluded unreadable slot, a registry owner recorded against
   that slot, and a cycle run: `_release_guard_reason` still returns
   `code_still_programmed` and the owner is retained.
2. The same with the owner **orphaned** — its `entry_id` absent from
   `hass.config_entries.async_entries(DOMAIN)` — run through
   `async_clear_orphans`: the code is retained, satisfying the SC-014 clause
   about an exclusion set by a different config entry.
3. A source-level assertion that `build_cycle_observation` contains no
   reference to exclusions, so a future refactor cannot quietly move the filter
   to the source.

### Mandatory: the gate is unchanged for non-excluded slots

`tests/unit/test_exclusion_gate.py` runs each of the three gate members with an
empty excluded set and asserts byte-identical behaviour to today, then with a
non-empty set and asserts only the excluded slot's contribution disappears.
SC-006 is measured here. It also covers FR-015 directly: an entry with one
excluded dead slot **and** one unaccounted readable coded slot stays pending
adoption (SC-015).

### Mandatory: end-to-end recovery

`tests/integration/test_exclusion_recovery.py` drives the whole SC-012 arc in
one test, with no manual intervention after the single service call:

1. one entry, one managed slot `UNKNOWN`, reservations refused with
   `unaccounted_slots`;
2. `set_slot_exclusion` for that slot;
3. next cycle: remaining reservations receive codes, the excluded slot is never
   written to, and the unreadable-slot notification is dismissed while the
   exclusion notification stands;
4. the slot becomes readable;
5. next cycle: the exclusion is gone from the persisted entry data, the
   notification is dismissed, and the slot is back under the full fail-closed
   gate;
6. the slot becomes unreadable again: the entry freezes again and the operator
   must decide afresh (US3 acceptance scenario 3).

### Other required coverage

- **US2 multi-entry** (`test_exclusion_multi_entry.py`): three entries, one
  with an `UNKNOWN` slot, each with a new reservation holding no existing
  registry allocation so that reuse cannot mask the gate; assert `B` and `C`
  refused with `adoption_pending` and `A` submitting no request at all; then
  one exclusion on `A`; then all three issuing; then a restart with no further
  action (SC-002).
- **Guards** (`test_exclusion_service.py`): each of `slot_readable`,
  `slot_claimed`, `slot_out_of_range` refused with nothing persisted
  (SC-005, FR-013); the schema contains no `force` key; both-targets and
  no-targets refused; repeat set and repeat clear both succeed reporting
  `changed: false` with no write.
- **Dry run**: every refusal and every success path exercised with
  `dry_run: true`, asserting zero entry writes, zero notification calls, and an
  unchanged next cycle (SC-007).
- **Persistence tolerance** (`test_slot_exclusions.py`): a missing key, `null`,
  a string, a list of integers, a list of malformed objects, a slot outside the
  range, and a slot that no longer exists all load to a usable set and allow
  setup to complete (FR-032, SC-010).
- **No reload loop**: persisting an exclusion while `config_entry.options` is
  empty invokes `update_listener` and returns without rebuilding the entry.
- **`force_reissue` unblocked** (SC-011): a preview on a non-excluded slot of
  an entry whose only unreadable slot is excluded returns a code rather than
  `unaccounted_slots`.
- **Notifications** (`test_exclusion_notifications.py`): exactly one standing
  notification per entry with a non-empty set and zero otherwise, at the end of
  every cycle (SC-008); the unreadable-slot notification reports only
  non-excluded slots; both are dismissed on unload and removal.
- **No codes anywhere** (SC-009): response, notification message, diagnostics
  payload, and captured log records asserted free of any code value.
- **Planner untouched**: an excluded slot still classifies as
  `ActionKind.BLOCKED, "unreadable"` and is never offered as free (SC-004).

## Risks and mitigations

| Risk | Severity | Mitigation |
|------|----------|------------|
| **Filtering in `build_cycle_observation`.** The obvious implementation releases codes on the strength of an exclusion (FR-018, SC-014). | **Critical** | The whole threading design; a dedicated mandatory regression; a source-level assertion that the observation builder never references exclusions; the hazard is documented at the top of `quickstart.md`. |
| **Missing gate member G3.** Fixing only `unaccounted_slots` and `_adoption_complete` leaves `force_reissue` blocked and FR-019 unmet, and the omission is invisible outside the preview path. | High | G3 is a named, separately-tested deliverable with its own task; SC-011 is an explicit test. |
| **Repeating the 3.5.0 persisted-state wedge.** Version 3.5.0 made persisted slot state authoritative, mass-cleared working codes, and permanently wedged; reverted in `0622057` and redesigned stateless by #607. | High | The three structural properties the spec requires: the set only ever *widens* what can be issued and can never cause a write or a clear; it is re-validated against the live observation every cycle; it self-clears. `revalidate` runs unconditionally before any consumer, and a test asserts that an exclusion whose slot is readable does not survive one cycle. If a future change makes the set load-bearing, skips re-validation, or lets an exclusion outlive its condition, that change is reintroducing the 3.5.0 failure and must be rejected. |
| **An exclusion is read as proof the slot is empty.** | High | FR-018; `build_cycle_observation` already records no code for an `UNKNOWN` slot, and the release guards keep seeing the raw unreadable set. Covered by the mandatory regression's orphan case. |
| **Config-entry write triggering a reload loop.** | Medium | `update_listener` returns early when `options` is empty (`__init__.py:259-261`); persistence is a no-op when the serialised value is unchanged; a regression test pins both. |
| **An exclusion silently surviving an options-flow save.** | Medium | It cannot: `update_listener` rebuilds `data` from `options`, so exclusions are dropped and the entry returns to fail-closed. Documented in the spec's Edge Cases and in the service description so operators expect it. |
| **Two notifications contradicting each other about one slot.** | Medium | The unreadable-slot notification is narrowed to the gated set, making the two subjects disjoint; asserted in `test_exclusion_notifications.py`. |
| **Operator excludes a slot that is merely in a long outage.** | Medium (accepted) | Inherent and accepted: FR-030 states the residual risk at the point of decision and keeps it standing in the notification; FR-020 auto-clear returns the slot to the gate the moment it reports again. |
| **Operator excludes every managed slot.** | Low | The gate is satisfied but reconciliation has no usable slot, so reservations report ordinary exhaustion rather than silent success; covered by an edge-case test. |
| **Divergence between the service's notion of "claimed" and the gate's.** | Low | Both call one extracted `claimed_slots` helper; the gate's call site is changed to use it so there is only one implementation. |
| **Line citations drifting again.** | Low | [research.md](research.md) records the live positions at `edb6a7f` and the corrections; the first implementation task re-reads every signature from source before any code is written. |

## Phase 0 Research Output

Complete. See [research.md](research.md) — ten decisions with rationale and
rejected alternatives, plus the corrected source citations. No open questions
and no `NEEDS CLARIFICATION` markers remain.

## Phase 1 Design Output

Complete:

- [data-model.md](data-model.md) — new and changed entities, persisted shape,
  state transitions, validation rules.
- [contracts/set-slot-exclusion-service.md](contracts/set-slot-exclusion-service.md)
  — service declaration, validation order, response shapes, gate API delta, and
  the invariants the contract asserts.
- [quickstart.md](quickstart.md) — implementation order, the traps, and the
  validation gate.

## Post-Design Constitution Check

Re-checked after Phase 1 with no change in status. The design adds four new
modules, all under the 400-line limit and each with one responsibility; adds no
configuration; adds no persisted store; and leaves every existing call site
compiling through defaulted parameters. Principle VI is satisfied by following
the `force_reissue` service precedent exactly where the spec does not require a
difference, and by documenting each of the three places it does.

**Gate result: PASS** — no violations, so Complexity Tracking stays empty.

## Complexity Tracking

> Fill ONLY if Constitution Check has violations that must be justified.

No violations. No entries.

## Spec gaps found while planning

Two, both minor, neither requiring a spec change:

1. **`_owner_may_be_programmed` does not exist.** The spec names it in FR-016
   and in Assumptions. The real method is
   `DoorCodeAllocator._owner_still_programmed`
   (`allocator/allocator.py:378-408`), reached through `_release_guard_reason`
   (`:346-376`). The requirement is unambiguous and correct about *what* must
   not change; only the identifier is wrong. The plan and tasks use the live
   name.
2. **FR-006 is silent on the persisted shape of `reason`.** Recording a reason
   per exclusion implies the persisted value cannot be a bare `list[int]`. The
   plan resolves this as a list of objects (research item 5) rather than
   leaving it to the implementer; nothing in the spec contradicts that, and
   FR-032's tolerance requirement makes the richer shape safe.

Several line citations in the spec had drifted past `6a6602b`; they are
corrected in [research.md](research.md) and are a documentation matter, not a
semantic one.

## Phase Notes

- **Phase 0** (research) and **Phase 1** (design) are complete in this PR.
- **Phase 2** (tasks) is [tasks.md](tasks.md), committed separately in this
  same PR, following the feature 023 precedent where `plan.md` and its
  supporting documents land in one commit and `tasks.md` in another.
- **Phase 3** (implementation) is out of scope here and happens on the
  `024-slot-exclusion` branch against issue #802.
