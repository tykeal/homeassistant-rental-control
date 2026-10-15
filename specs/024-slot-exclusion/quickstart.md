<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Quickstart: Implementing Slot-Exclusion Recovery Control

**Feature**: `024-slot-exclusion` | **Plan**: [plan.md](plan.md) |
**Tasks**: [tasks.md](tasks.md) | **Issue**: #802

## Read this first

There is one way to implement this feature that looks obviously right and is
dangerous. `build_cycle_observation`
(`coordinator_helpers/code_allocation.py:262-282`) is the single place
`unreadable_slots` is computed, so subtracting the excluded set there is the
natural one-line change.

**Do not do that.** `DoorCodeAllocator._owner_still_programmed`
(`allocator/allocator.py:378-408`) reads `unreadable_slots` as *evidence that a
code may still be physically programmed*, and it is what stops
`_release_guard_reason` (`:346-376`) from returning a code to the pool.
Filtering at the source makes an excluded slot look empty, and the orphan sweep
will release a code that is plausibly still live on the lock. That is FR-018
violated and SC-014 blown, and it is worse than the freeze this feature exists
to lift.

The excluded set travels **beside** the observation. See
[plan.md](plan.md#why-the-filter-is-not-in-the-observation).

## Before you start

Re-read these live signatures from source and adjust the tasks where source and
the documents disagree. The specification's citations drifted past `6a6602b`;
[research.md](research.md#appendix-corrected-citations) records the
corrections, and they may drift again.

| What | Where |
|------|-------|
| `unaccounted_slots`, `resolve_cycle`, `allocate_request`, `_track_pending_adoption_slots` | `custom_components/rental_control/allocator/issuance.py` |
| `_release_guard_reason`, `_owner_still_programmed` | `custom_components/rental_control/allocator/allocator.py` |
| `preview_request_and_result` | `custom_components/rental_control/allocator/reissue_preview.py` |
| `register_allocator_services`, `_collect_observations`, `report_forced_hold_deferrals` | `custom_components/rental_control/allocator/services.py` |
| `allocator_diagnostics` | `custom_components/rental_control/allocator/diagnostics.py` |
| `CycleRequest`, `CycleObservation` | `custom_components/rental_control/allocator/models.py` |
| `async_resolve_codes`, `_adoption_complete`, `build_cycle_observation`, `report_unreadable_slots`, `dismiss_unreadable_slots` | `custom_components/rental_control/coordinator_helpers/code_allocation.py` |
| `CodeResolutionRequest`, `coerce_code_resolution_request` | `custom_components/rental_control/coordinator_helpers/reissue.py` |
| `resolve_entity_target`, `resolve_slot_target`, `_entry_from_sensor_state` | `custom_components/rental_control/coordinator_helpers/reissue_targets.py` |
| `register_force_reissue_service`, `FORCE_REISSUE_SCHEMA`, `_resolve_and_validate_target`, `_refusal` | `custom_components/rental_control/allocator/reissue_service.py` |
| `update_listener` | `custom_components/rental_control/__init__.py` |
| `classify_slot` and the two `SlotStatus.UNKNOWN` sites | `custom_components/rental_control/coordinator_helpers/keymaster_observation.py` |

## Suggested commit sequence

Each line is one atomic commit. The ordering is a **safety** ordering, not a
convenience one: see [tasks.md](tasks.md) for why.

1. `Feat(const): Add excluded slots config key` — `CONF_EXCLUDED_SLOTS` plus
   the pure `allocator/exclusions.py` parser, serialiser, and guard evaluation,
   with their unit tests. Nothing consumes them yet.
2. `Refactor(allocator): Extract claimed slot lookup` — lift `claimed_slots`
   out of `unaccounted_slots` with no behaviour change, so the gate and the
   service share one definition.
3. `Feat(allocator): Exclude slots from the issuance gate` — all three gate
   members together, plus the two dataclass fields that carry the set, plus the
   release-evidence regression. **These ship as one commit**; a tree with one
   or two of the three is a tree where `force_reissue` or adoption behaves
   inconsistently with issuance for the same slot.
4. `Feat(coordinator): Revalidate slot exclusions per cycle` —
   `coordinator_helpers/slot_exclusions.py` load, re-validate, persist, and the
   `async_resolve_codes` wiring, including narrowing the existing
   unreadable-slot notification to the gated set.
5. `Feat(allocator): Add set_slot_exclusion service` — resolver, handler,
   schema, `services.yaml`, `strings.json`, both translations.
6. `Feat(allocator): Report excluded slots to operators` — the standing
   notification, the dismissals in `__init__.py`, and the diagnostics key.
7. `Test(exclusion): Add end-to-end recovery coverage` — the US1→US3 arc, the
   US2 multi-entry scenario, and the notification lifecycle.

Commit 3 must not land before commit 2, and commit 5 must not land before
commits 3 and 4: the service is the only thing that can create an exclusion,
and it must not exist before the machinery that honours and retires one.

## Traps that will cost you a day each

1. **Filtering in `build_cycle_observation`.** See above. Everything else on
   this list is a bug; this one is a security incident.
2. **Forgetting the third gate member.** `allocator/reissue_preview.py:111-112`
   rejects any non-empty raw unreadable set *before* it calls
   `unaccounted_slots` at line 113. Fixing the helper does not fix the preview.
   SC-011 is the test that catches this.
3. **Writing the key to `options`.** `update_listener` (`__init__.py:256-281`)
   rebuilds `data` from `options.copy()` and then clears `options`. A key in
   `options` gets promoted into `data` and erased from `options` on the next
   save. It goes in `data`.
4. **Expecting a reload loop from `async_update_entry`.** There isn't one:
   `update_listener` returns immediately when `config_entry.options` is empty.
   Write the regression test anyway; the guard is three lines from the thing it
   protects.
5. **Narrowing the whole of `_adoption_complete`.** Only the
   `observation.unreadable_slots` branch is exclusion-aware. The
   `readable_coded_slots <= adopted_slots` condition below it must stay exactly
   as it is, or an entry with an unaccounted readable coded slot will complete
   adoption when it must not (FR-015, SC-015).
6. **Adding a `force` flag out of habit.** `force_reissue` has one. This
   service must not. Three guards, no override, by FR-009 and the spec's Out of
   Scope section.
7. **Reusing `resolve_slot_target`.** It refuses with `slot_not_managed` before
   you can emit `slot_out_of_range`, resolves an identity an exclusion has no
   use for, and cannot express FR-012's "no guards when clearing".
8. **Leaving the `6a6602b` notification on the raw set.** It says issuance is
   withheld. For an excluded slot that is false, and the operator ends up with
   two standing notifications contradicting each other about one slot.
9. **Caching the excluded set on the coordinator.** FR-024 requires the gate's
   set and the re-validated set to come from one observation in one place. A
   cached copy is a divergence waiting to happen.
10. **Re-validating or persisting in the dry-run or preview path.** Dry run
    must be inert (FR-007, SC-007). The ordinary cycle auto-clears moments
    later anyway.
11. **Touching `reconciliation/`.** Nothing there changes. `_classify_slot`
    (`reconciliation/desired.py:258-259`) already blocks an `UNKNOWN` slot,
    which is FR-017 and SC-004 for free — and only while exclusion stays out of
    the planner's view.

## Validation gate

```bash
uv run pytest tests/ -q -p no:randomly
uv run ruff check custom_components/ tests/
uv run pre-commit run --all-files
```

- Every new Python file needs the `#`-comment SPDX header pair; every new
  markdown file needs the `<!-- -->` form.
- Coverage floor is 95%.
- Never pass `--no-verify`, `--no-gpg-sign`, or `git reset` after a failed
  commit. Fix, `git add`, and commit again with the same message.
- `pytest-randomly` is not installed in this environment; `-p no:randomly` is
  harmless, but do not pass `--randomly-seed`.

## Manual verification against a live freeze

1. Make one managed slot unreadable — delete or disable its Keymaster PIN
   entity. Confirm the entry refuses allocations with `unaccounted_slots` and
   that the unreadable-slot notification appears.
2. Restart Home Assistant and confirm every other entry now refuses with
   `adoption_pending` while the affected entry submits nothing. This is regime
   2 and is the damaging one.
3. Call `rental_control.set_slot_exclusion` with `dry_run: true` and confirm
   the response describes the resulting set and that nothing changed.
4. Call it for real. Confirm within one refresh cycle that issuance resumes for
   every entry, that the unreadable-slot notification is gone, and that the
   exclusion notification stands and names the residual risk.
5. Confirm the excluded slot is never written to in the Keymaster entities.
6. Restart. Confirm nothing re-freezes and no further action is needed.
7. Restore the Keymaster entity. Confirm the exclusion disappears from the
   entry's stored data within one cycle, the notification is dismissed, and the
   slot is back under the ordinary gate.
8. Save the options flow with an exclusion standing, and confirm the exclusion
   is dropped and the entry returns to fail-closed. This is expected.
