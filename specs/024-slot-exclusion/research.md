<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Phase 0 Research: Slot-Exclusion Recovery Control

**Feature**: `024-slot-exclusion` | **Spec**: [spec.md](spec.md) | **Issue**: #802

Every finding below was read out of live source on `main` at `edb6a7f`, which
is the commit that merged the specification. Several line numbers quoted in the
specification were written against `main` before `6a6602b` ("Feat(allocator):
Notify on unreadable lock slots") landed and have since drifted; the corrected
citations are recorded in [Appendix: corrected citations](#appendix-corrected-citations)
and used everywhere in the plan.

## 1. Where exactly is the gate, and how many members does it have?

**Decision**: Three gate members, no more. They are enumerated below with live
citations and are the only places this feature subtracts excluded slots.

| # | Gate member | Location at `edb6a7f` | What it does with `unreadable_slots` |
|---|-------------|------------------------|---------------------------------------|
| G1 | `unaccounted_slots` | `allocator/issuance.py:239-253` | Returns `observation.unreadable_slots - claimed`; a non-empty result sets `issuance_allowed` false for the cycle (`issuance.py:157`, `:170`) and refuses every allocation with `reason="unaccounted_slots"` (`issuance.py:77`). |
| G2 | `_adoption_complete` | `coordinator_helpers/code_allocation.py:241-259` | `if observation.unreadable_slots: return False` at line 249-250. A never-completing adoption keeps the entry in the singleton `_pending_adoption` set, refusing **every** entry with `reason="adoption_pending"` (`issuance.py:78-79`). |
| G3 | re-issue preview raw rejection | `allocator/reissue_preview.py:111-112` | `if observation.unreadable_slots: return ReissuePreview(reason="unaccounted_slots")`. This is an independent check that runs **before** it calls `issuance.unaccounted_slots` at line 113-114, so making G1 exclusion-aware does not make the preview exclusion-aware. |

**Rationale**: G3 is easy to miss because the preview also consumes G1. The
specification calls this out in FR-016 and FR-019, and the source confirms the
duplication: `reissue_preview.py:111` tests the raw observation, `:113` tests
the helper. Both must learn about exclusions or `force_reissue` stays blocked.

**Alternatives rejected**: Collapsing G3 into G1 by deleting the raw check at
`reissue_preview.py:111-112`. Rejected because the raw check is strictly
stronger than G1 — it refuses even when every unreadable slot is claimed by a
registry owner — and this feature is not chartered to change gate semantics for
non-excluded slots (FR-016, SC-006). Narrowing the raw check by the excluded
set only is the minimal change that satisfies FR-019.

## 2. Where does the filtering happen: the observation, or the gate?

**Decision**: At the gate. `build_cycle_observation`
(`coordinator_helpers/code_allocation.py:262-282`) keeps producing the raw,
unfiltered `unreadable_slots` set, and the excluded set travels beside the
observation rather than inside it.

**Rationale**: `CycleObservation.unreadable_slots` has readers outside the gate
that treat it as **evidence a code may still be programmed**, not as a reason
to refuse issuance. The decisive one is
`DoorCodeAllocator._owner_still_programmed`
(`allocator/allocator.py:378-408`):

```python
            programmed = (
                record.code in observation.observed_codes
                or owner.slot in observation.unreadable_slots
            )
```

An unreadable slot is counted as *possibly still holding the code*, which is
what stops `_release_guard_reason` (`allocator/allocator.py:346-376`) from
returning a code to the pool. The same observations are rebuilt for orphan
cleanup by `services._collect_observations`
(`allocator/services.py:98-112`) and handed to `async_clear_orphans`.

If exclusion were baked into the observation, an excluded slot would silently
disappear from that evidence set, `_owner_still_programmed` would return
`False`, and the orphan sweep could release a code that is very plausibly still
physically programmed on the lock — the exact outcome FR-018 and SC-014
prohibit, and worse than the freeze this feature exists to lift.

**Alternatives rejected**:

- *Filtering inside `build_cycle_observation`.* Rejected per above. This is the
  obvious implementation and it is wrong; the plan says so loudly because it is
  the single most likely way to implement this feature incorrectly.
- *A second "gated observation" object built alongside the raw one.* Rejected
  as a near-miss of the same hazard: two observation objects with the same type
  and different truth value is an invitation to pass the wrong one to
  `CycleRequest`, and the type system cannot tell them apart. Carrying a
  `frozenset[int]` of excluded slots is unambiguous at every call site.

## 3. How does the excluded set reach the gate?

**Decision**: As an explicit, additively-defaulted field on the request objects
that already cross each boundary:

- `CodeResolutionRequest.excluded_slots: frozenset[int] = frozenset()`
  (`coordinator_helpers/reissue.py:81-91`)
- `CycleRequest.excluded_slots: frozenset[int] = frozenset()`
  (`allocator/models.py:193-203`)
- a keyword-only `excluded: frozenset[int] = frozenset()` parameter on
  `unaccounted_slots` and on `_adoption_complete`

**Rationale**: Both dataclasses are `frozen=True, slots=True` and both already
grew one optional field for feature 023 (`forced_reissues`), so the pattern is
established, the default keeps every existing construction site compiling, and
the empty default is the fail-closed value. A defaulted empty frozenset is also
the correct value for every non-coordinator caller — the orphan sweep, the
removal path, and tests — none of which should ever apply an exclusion.

**Alternatives rejected**: Reading the config entry from inside the allocator.
Rejected because the allocator is the system-wide singleton and must not
acquire a per-entry configuration dependency; `DoorCodeAllocator` already has
`hass`, which makes this tempting and still wrong. Keeping the lookup in the
coordinator helper preserves the feature-022 boundary that the registry-facing
half stays free of config-entry concerns.

## 4. Where is the set persisted?

**Decision**: Config entry `data`, under a new `CONF_EXCLUDED_SLOTS =
"excluded_slots"` in `const.py`, written through
`hass.config_entries.async_update_entry`.

**Rationale**: `update_listener` (`__init__.py:256-281`) rebuilds the entry on
every options-flow save:

```python
    new_data = config_entry.options.copy()
    new_data.pop(CONF_GENERATE, None)
    ...
    hass.config_entries.async_update_entry(
        entry=config_entry,
        unique_id=config_entry.unique_id,
        data=new_data,
        title=new_data[CONF_NAME],
        options={},
    )
```

`data` is replaced wholesale from `options` and `options` is then emptied. A
key written to `options` would be promoted into `data` on the next save and
erased from `options`, giving it two homes and no benefit. A key written to
`data` survives restarts (FR-029) and is dropped by an options-flow save, which
fails in the safe direction and is documented as expected behaviour in the
spec's Edge Cases.

**A verified non-problem**: writing `data` with `async_update_entry` fires the
entry's update listeners, which includes this integration's `update_listener`.
That function's first statement is `if not config_entry.options: return`
(`__init__.py:259-261`). Outside an options-flow save `options` is empty, so a
persistence write returns immediately and cannot cause a reload loop. The plan
still requires a regression test pinning this, because the guard is three lines
away from the thing it protects.

**Alternatives rejected**:

- *The reconciliation `Store`.* Rejected outright. That store is cache-only and
  never load-bearing by the #607 redesign, and putting operator intent in it
  would make its contents matter — reintroducing precisely the coupling that
  `0622057` reverted.
- *A dedicated new `Store`.* Rejected as unnecessary surface: a new store needs
  a schema, a version, a migration, and a loss path, for a few integers that
  already have a natural home beside `start_slot` and `max_events`.
- *Runtime-only, in-memory.* Rejected by the spec (FR-029): the regime-2 freeze
  is re-established at every startup, so a runtime-only exclusion is erased by
  exactly the event that recreates the problem.

## 5. What is the persisted shape, and how tolerant must loading be?

**Decision**: A JSON list of objects, loaded through a total parser that never
raises:

```json
"excluded_slots": [{"slot": 7, "reason": "lock replaced 2026-10-12"}]
```

parsed to `dict[int, str | None]` keyed by slot.

**Rationale**: FR-006 requires an optional operator reason carried with the
exclusion, so a bare `list[int]` cannot express the feature. A list of objects
is stable under JSON round-tripping, is diff-readable in `.storage`, and leaves
room for a later field without a migration. FR-032 and SC-010 require that a
malformed, unknown-slot, or otherwise unplaceable entry be **dropped rather
than raised**: loading exclusions must never prevent an entry from setting up.
The parser therefore ignores non-list input, non-mapping members, missing or
non-integer `slot` values, and non-string reasons, and never raises.

**Alternatives rejected**: `dict[str, str]` keyed by stringified slot.
Rejected because JSON object keys are strings while every slot in the codebase
is an `int`, and the conversion would have to happen at every boundary; a list
of records keeps the `int` conversion in exactly one function.

## 6. How does re-validation stay in step with the gate?

**Decision**: Re-validate inside `async_resolve_codes`
(`coordinator_helpers/code_allocation.py:46-146`), immediately after
`build_cycle_observation` at line 68 and before anything consumes the excluded
set, using that same observation object.

**Rationale**: FR-024 requires that the set the gate uses and the set that is
re-validated can never diverge. Doing both from one observation in one function
makes divergence structurally impossible rather than merely unlikely. An
exclusion is dropped when either of two things is true of that observation:

1. the slot is not in `observation.unreadable_slots` — it is readable again,
   so FR-020 auto-clear applies; or
2. the slot is not in `observation.managed_slots` — the managed range moved,
   so FR-023 applies.

Note that condition 1 is expressed against the observation's unreadable set,
not against a `SlotStatus` lookup, because `build_cycle_observation` already
derives `unreadable_slots` as exactly `status is SlotStatus.UNKNOWN` over
managed slots (`code_allocation.py:278-280`). Using the observation keeps one
definition of "unreadable" in the feature instead of two.

**Alternatives rejected**: Re-validating in the coordinator refresh shell
before calling `async_resolve_codes`. Rejected because the shell does not hold
the observation and would have to build a second one, which is both wasted work
and a divergence risk.

## 7. Does the preview path re-validate or persist?

**Decision**: No. The preview (`allocator/reissue_preview.py`) and the service's
dry-run mode read the current persisted set, apply it to the gate, and write
nothing — no persistence, no auto-clear, no notification (FR-007, SC-007).

**Rationale**: Dry run must be observably inert. Auto-clear is a per-cycle
responsibility and the ordinary cycle will perform it moments later anyway, so
nothing is lost by the preview declining to do it.

## 8. How should this sit beside the `6a6602b` unreadable-slot notification?

**Decision**: Narrow the existing notification, then add a second, distinct one.

`report_unreadable_slots` (`coordinator_helpers/code_allocation.py:160-195`)
was added by `6a6602b` and is called from `async_resolve_codes` at line 120-122
with `sorted(observation.unreadable_slots)`. Its message states that "Door code
issuance is withheld while those slots stay unreadable". For an **excluded**
slot that sentence is false, and a notification that lies about the current
state is worse than no notification.

So:

- the existing call passes the **gated** set — `observation.unreadable_slots -
  excluded` — so the "issuance withheld" notification describes exactly the
  slots that are actually withholding issuance, and dismisses itself when the
  last non-excluded unreadable slot is excluded or recovers;
- a new, separately-identified per-entry notification covers the excluded set
  and states the FR-030 residual risk, which is a different message with a
  different lifetime and a different call to action.

**Rationale**: The two notifications answer different operator questions —
"why has issuance stopped?" versus "what risk am I currently carrying?" — and
their lifetimes are disjoint by construction, since a slot is in exactly one of
the two sets. Merging them would produce a single message that has to describe
both states at once and change its meaning per slot.

`6a6602b` also added `_track_pending_adoption_slots`
(`allocator/issuance.py:225-237`), which records "the unreadable slots blocking
an entry's adoption". After this feature, an excluded slot no longer blocks
adoption, so that tracker is fed the gated set too. It is diagnostic, not
evidential, so narrowing it carries none of the FR-018 hazard from research
item 2.

**Alternatives rejected**: Leaving `report_unreadable_slots` on the raw set and
relying on the new notification to explain the contradiction. Rejected: the
operator would hold two standing notifications naming the same slot, one saying
issuance is frozen and one saying it is not.

## 9. Can the existing re-issue target resolver be reused?

**Decision**: Partly. Extract the entity-to-entry lookup and write a dedicated
exclusion resolver for the rest.

`coordinator_reissue_targets.resolve_slot_target`
(`coordinator_helpers/reissue_targets.py:71-111`) is close but wrong for this
feature on three counts: it refuses with `slot_not_managed` before the service
can emit the FR-011 `slot_out_of_range` reason, it resolves an identity and a
checked-in flag that an exclusion has no use for, and FR-012 requires the range
check to be skipped entirely when **clearing** an exclusion — which
`resolve_slot_target` cannot express.

The entity path, however, needs exactly what `_entry_from_sensor_state`
(`reissue_targets.py:127-144`) already does. That is lifted to a public
`entry_id_for_entity(hass, entity_id)` in the same module — a pure rename plus
export, with no behaviour change — and both features call it.

**Alternatives rejected**: Copying the sensor-attribute lookup into the new
module. Rejected: two copies of an attribute contract that the captive-portal
integration also depends on is a maintenance trap.

## 10. Does anything in `reconciliation/` change?

**Decision**: No. Not one line.

**Rationale**: `_classify_slot` (`reconciliation/desired.py:258-259`) already
maps `SlotStatus.UNKNOWN` to `ActionKind.BLOCKED` with reason `"unreadable"`,
so an excluded slot is already never written to, never cleared, and never
offered as free. That is FR-017 and SC-004 satisfied with zero code, and it is
only true because exclusion deliberately does **not** reach the planner. The
plan's test strategy pins this with an assertion rather than trusting it.

## Appendix: corrected citations

The specification was written against pre-`6a6602b` line numbers. These are the
live positions at `edb6a7f`, and the plan uses these:

| Referenced as | Actual at `edb6a7f` | Note |
|---------------|---------------------|------|
| `unaccounted_slots` at `allocator/issuance.py:221-235` | `allocator/issuance.py:239-253` | Shifted by `_track_pending_adoption_slots`. |
| `_adoption_complete` at `code_allocation.py:187-205` | `code_allocation.py:241-259` | Shifted by the `6a6602b` notification helpers. |
| `build_cycle_observation` at `code_allocation.py:208-227` | `code_allocation.py:262-282` | Same cause. |
| claim subtraction at `issuance.py:227-235` | `issuance.py:245-253` | Same cause. |
| `_owner_may_be_programmed` at `allocator/allocator.py:370-394` | `_owner_still_programmed` at `allocator/allocator.py:378-408` | **The method does not exist under the name the spec uses.** The guard that calls it is `_release_guard_reason` at `allocator/allocator.py:346-376`. |
| orphan observation collection at `allocator/services.py:98-113` | `allocator/services.py:98-112` | Correct as written. |
| `update_listener` at `__init__.py:252-278` | `__init__.py:256-281` | Minor drift. |
| `_classify_slot` UNKNOWN branch at `reconciliation/desired.py:257-258` | `reconciliation/desired.py:258-259` | Off by one. |
| `register_allocator_services` at `allocator/services.py:39-57` | `allocator/services.py:39-57` | Correct as written. |
| preview raw rejection at `allocator/reissue_preview.py:111-112` | `allocator/reissue_preview.py:111-112` | Correct as written. |
| `report_forced_hold_deferrals` at `allocator/services.py:164-225` | `allocator/services.py:164-226` | Correct as written. |
| allocator diagnostics at `allocator/diagnostics.py:18-58` | `allocator/diagnostics.py:18-58` | Correct as written. |
| `ObservedSlot.__post_init__` at `reconciliation/stateless_models.py:50-77` | `reconciliation/stateless_models.py:50-77` | Correct as written. |
| `SlotStatus` at `reconciliation/enums.py:19-45` | `reconciliation/enums.py:19-45` | Correct as written. |

One further correction, in the opposite direction:
`coordinator_helpers/keymaster_observation.py` **does exist** (131 lines, with
the two `SlotStatus.UNKNOWN` construction sites at lines 59 and 72). The
specification does not cite it, but the planning brief asserted its absence;
the plan treats it as present and as the upstream producer of the `UNKNOWN`
classification this feature keys on. Nothing in it changes.
