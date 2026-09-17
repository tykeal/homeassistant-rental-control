<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Quickstart: Implementing Force Re-Issue

Implementation and validation guide for `023-force-reissue`. Read
[plan.md](plan.md) first; this file is the order of work and the gate.

## Before you start

Re-read these files in the tree you are about to change. Do not trust this
document, or the plan, over the source:

- `custom_components/rental_control/allocator/allocator.py` —
  `_release_guard_reason`, `_owner_still_programmed`, `_sweep_unlocked`,
  `async_mark_entry_removed`, `async_clear_orphans`
- `custom_components/rental_control/allocator/registry.py` — `release`,
  `is_available`, `add_owner`, `allocate`
- `custom_components/rental_control/allocator/issuance.py` — `resolve_cycle`,
  `allocate_request`, `observed_alias_key`, `unaccounted_slots`
- `custom_components/rental_control/allocator/adoption.py` —
  `_release_moved_observed_alias`, `_report_identity_mismatch`
- `custom_components/rental_control/coordinator_helpers/reservations.py` —
  `_resolve_observed_code`
- `custom_components/rental_control/coordinator_helpers/code_allocation.py` —
  `build_adoption_requests`, `_adoption_complete`, `build_cycle_observation`
- `custom_components/rental_control/coordinator.py` — `get_slot_code`
- `custom_components/rental_control/coordinator_helpers/keymaster_observation.py`
  — the two `SlotStatus.UNKNOWN` construction sites

## Suggested commit sequence

Each step is one atomic commit that builds and passes the suite.

1. **Guard exemption and hold identity.** Add `ForcedReleaseExemption`,
   `ForcedReissueDirective`, `ReissueOutcome`, and `ReissuePreview` to
   `allocator/models.py`; add `allocator/reissue.py` with the hold-key helpers
   and `_conflict_exempt`; add the keyword-only `forced_release` parameter to
   `_release_guard_reason`; skip hold owners in `_sweep_unlocked`. Ship
   `tests/unit/test_allocator_release_guard.py` in the same commit — it is the
   regression that proves the three ordinary paths are untouched.
2. **Cycle wiring.** `CycleRequest.forced_reissues`, `CycleResult.reissues`,
   `apply_forced_reissues`, `release_forced_holds`, and the two new steps in
   `issuance.resolve_cycle`. Extract `select_code` from `allocate_request`
   without changing its behaviour.
3. **Suppression.** `coordinator_helpers/reissue.py` with `ReissueSuppression`
   and `PendingReissue`; the `ReservationBuildContext` field and its default;
   `_resolve_observed_code`; `build_protected_reservation`;
   `build_adoption_requests` skip; the `_adoption_complete` exclusion.
4. **Service.** `allocator/reissue_service.py`, registration from
   `allocator/services.py`, `services.yaml`, `strings.json`, and both
   translations. Target resolution and all nine validation checks.
5. **Dry run.** `async_preview_reissue` and the separate preview response
   builder.
6. **Reporting.** The three audit log lines, the deferred-release notification,
   the allocator diagnostics counters, and the identity-mismatch suppression in
   `adoption.py`.

## Traps that will cost you a day each

- **Suppressing the adoption request without fixing `_adoption_complete`.**
  `readable_coded_slots <= adopted_slots` goes `False`, `allocations` is emptied
  for the *whole entry*, and nothing is issued to anyone that cycle. The
  re-issue looks like it silently did nothing. Exclude suppressed slots from
  `readable_coded_slots`.
- **Forgetting that the identity still owns the old code.**
  `registry.allocate()` returns the existing record for a known identity before
  anything else runs. Re-home the owner to the hold first, or the allocation is
  a no-op.
- **Calling a public allocator method from inside `resolve_cycle`.** `_lock` is
  not re-entrant. The new steps must be non-locking helpers, exactly like
  `_adopt_unlocked` and `_sweep_unlocked`.
- **Treating `SlotStatus.FREE` as unreadable, or `SlotStatus.UNKNOWN` as
  empty.** The first wedges issuance permanently; the second releases a live
  code. The only correct test is `status is SlotStatus.UNKNOWN`, and
  `blocked_reason` is diagnostic only — one of the two construction sites does
  not set it.
- **Widening the exemption "just for the sweep".** The sweep must keep the full
  guard. Hold owners are skipped by the sweep, not exempted within it.
- **Letting the raw code into the non-dry-run response.** Use two response
  builders, not one with a conditional field removal.

## Validation gate

Everything below must pass before the implementation PR is opened.

```bash
uv run ruff check custom_components/ tests/
uv run pytest tests/ -q -p no:randomly
uv run pre-commit run --all-files
```

Coverage floor is 95%. Never pass `--no-verify`.

Known flakes — do **not** "fix" them, and re-run them in isolation before
treating either as a regression:

- `tests/integration/test_refresh_cycle.py::test_missing_store_adopts_coded_slots_when_unavailable_at_setup`
- `tests/integration/test_refresh_cycle.py::test_deleted_store_reenable_recovers_coded_slots`

They share a fixture and fail together spuriously.

## Manual verification against the live duplicate

On a system reproducing the production condition — two entries with carved-out
ranges on one shared parent lock, both holding the same code:

1. `rental_control.force_reissue` with `dry_run: true` against one side's
   reservation sensor. Confirm the response names the code that would be issued
   and that the registry, the lock, and both sensors are completely unchanged.
2. Repeat without `dry_run`. Confirm the targeted sensor still shows the **old**
   code immediately afterwards — that is FR-026 working, not a failure — and
   that the log records the masked replaced and replacement refs.
3. Wait for the write to be confirmed. Confirm the targeted sensor flips to the
   new code, the other reservation is untouched, the old code's record has
   dropped from two owners to one, and no duplicate is reported for it.
4. Confirm no raw door code appears anywhere in the log for steps 2 and 3.
